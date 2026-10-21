"""Workflows. Every change goes through here so it is written to the audit log with the user's name."""
from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from datetime import date, datetime, timedelta

import pandas as pd

from . import engine
from .config import DEFAULT_PROGRAMS, Settings
from .dates import add_years, parse_date, quarter_bounds, quarter_of
from .db import SCHEMA, Backend, coltype, to_json
from .matcher import GREEN, MANUAL, PRIOR, RED, YELLOW, LineMatch, MatchResult, match_invoice
from .parsers import (ClassifiedFile, ParsedInvoice, classify_file, file_hash, parse_fa_register,
                      parse_invoice, parse_tracker, tracker_summary_total)
from .storage import FileStore
from .workbook import DECISIONS


def now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _none(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


class Service:
    def __init__(self, backend: Backend, files: FileStore, settings: Settings):
        self.db = backend
        self.files = files
        self.settings = settings

    # ------------------------------------------------------------------ reads
    def load(self) -> dict[str, pd.DataFrame]:
        return {t: self.db.read(t) for t in SCHEMA}

    def schedule_start(self, data: dict | None = None) -> date:
        data = data or {"settings": self.db.read("settings")}
        s = data["settings"]
        row = s.loc[s["key"] == "schedule_start", "value"]
        return parse_date(row.iloc[0]) if len(row) else self.settings.schedule_start

    def is_empty(self) -> bool:
        return self.db.read("contracts").empty

    # ------------------------------------------------------------------ audit
    def audit(self, user: str, action: str, entity: str, entity_id, field: str | None = None,
              old=None, new=None, reason: str | None = None) -> None:
        self.db.insert("audit_log", [{
            "audit_id": uuid.uuid4().hex, "ts": now(), "user_name": user, "action": action,
            "entity": entity, "entity_id": str(entity_id), "field": field,
            "old_value": None if old is None else (old if isinstance(old, str) else to_json(old)),
            "new_value": None if new is None else (new if isinstance(new, str) else to_json(new)),
            "reason": reason,
        }])

    def _update(self, user: str, table: str, key, changes: dict, current: dict, reason: str | None,
                action: str = "update") -> dict:
        """Update only fields that actually change, logging old -> new for each."""
        real = {}
        for k, v in changes.items():
            old = _none(current.get(k))
            v = _none(v)
            if _norm(old) != _norm(v):
                real[k] = v
                self.audit(user, action, table, key, k, _str(old), _str(v), reason)
        if real:
            stamp = {}
            if "updated_by" in dict(SCHEMA[table]):
                stamp = {"updated_by": user, "updated_at": now()}
            self.db.update(table, key, {**real, **stamp})
        return real

    def add_issue(self, user: str, category: str, entity: str, entity_id, description: str,
                  amount: float | None = None) -> None:
        self.db.insert("data_issues", [{
            "issue_id": uuid.uuid4().hex, "category": category, "entity": entity, "entity_id": str(entity_id),
            "description": description, "amount_impact": amount, "status": "Open",
            "created_by": user, "created_at": now(),
        }])

    def resolve_issue(self, user: str, issue_id: str, resolution: str) -> None:
        cur = self._row("data_issues", issue_id)
        self._update(user, "data_issues", issue_id,
                     {"status": "Resolved", "resolution": resolution, "resolved_by": user, "resolved_at": now()},
                     cur, resolution)

    def _row(self, table: str, key) -> dict:
        from .db import KEYS
        df = self.db.read(table)
        k = KEYS[table]
        hit = df[df[k].astype(str) == str(key)]
        if hit.empty:
            raise KeyError(f"{table} {key} not found")
        return hit.iloc[0].to_dict()

    # ------------------------------------------------------------------ setup
    def ensure_defaults(self, user: str = "system") -> None:
        progs = self.db.read("programs")
        missing = [p for p in DEFAULT_PROGRAMS if p["program_code"] not in set(progs["program_code"])]
        if missing:
            self.db.insert("programs", [{**p, "updated_by": user, "updated_at": now()} for p in missing])
        s = self.db.read("settings")
        if "schedule_start" not in set(s["key"]):
            self.db.insert("settings", [{"key": "schedule_start", "value": self.settings.schedule_start.isoformat(),
                                         "updated_by": user, "updated_at": now()}])

    def save_setting(self, user: str, key: str, value: str, reason: str, summary: dict | None = None) -> None:
        """Create or replace a setting. The audit log gets `summary` (or the value) rather than a large payload."""
        s = self.db.read("settings")
        if key in set(s["key"]):
            self.db.update("settings", key, {"value": value, "updated_by": user, "updated_at": now()})
            self.audit(user, "update", "settings", key, None, None, summary if summary is not None else value, reason)
        else:
            self.db.insert("settings", [{"key": key, "value": value, "updated_by": user, "updated_at": now()}])
            self.audit(user, "create", "settings", key, None, None, summary if summary is not None else value, reason)

    def update_program(self, user: str, code: str, changes: dict, reason: str) -> None:
        cur = self._row("programs", code)
        real = self._update(user, "programs", code, changes, cur, reason)
        if "annual_fee" in real:
            self._reprice_open_events(user, code, float(real["annual_fee"]), reason)

    def _reprice_open_events(self, user: str, code: str, fee: float, reason: str) -> None:
        ev = self.db.read("fee_events")
        c = self.db.read("contracts").set_index("fa_number")["annual_fee_override"]
        for r in ev[(ev["program_code"] == code) & ev["invoice_no"].isna()].itertuples():
            if pd.notna(c.get(r.fa_number)):
                continue
            self._update(user, "fee_events", r.event_id, {"expected_amount": fee}, r._asdict(), reason)

    # ------------------------------------------------------------------ contracts
    def next_fa_number(self, data: dict | None = None) -> int:
        c = (data or {}).get("contracts")
        c = self.db.read("contracts") if c is None else c
        # The register also holds legacy 8-digit sub-tranche numbers (e.g. 70539002); ignore them.
        main = c["fa_number"].dropna().astype(int)
        main = main[main < 100000]
        return int(main.max()) + 1 if len(main) else 70500

    def suggest_series_display(self, program_code: str, base_series: str, data: dict | None = None) -> tuple[str, int | None]:
        c = (data or {}).get("contracts")
        c = self.db.read("contracts") if c is None else c
        same = c[(c["program_code"] == program_code) & (c["base_series"] == base_series)].sort_values("fa_number")
        if same.empty:
            return base_series, None
        return f"{base_series} ({len(same) + 1})", int(same["fa_number"].iloc[0])

    def add_contract(self, user: str, contract: dict, reason: str = "New funding agreement",
                     generate: bool = True) -> int:
        c = self.db.read("contracts")
        fa = int(contract["fa_number"])
        if fa in set(c["fa_number"].dropna().astype(int)):
            raise ValueError(f"FA {fa} already exists.")
        if not contract.get("issue_date") or not contract.get("maturity_date"):
            raise ValueError("Issue (settlement) date and maturity date are required.")
        if contract["maturity_date"] <= contract["issue_date"]:
            raise ValueError("Maturity date must be after the issue date.")
        row = {col: contract.get(col) for col, _ in SCHEMA["contracts"]}
        row.update({"fa_number": fa, "created_by": user, "created_at": now(), "updated_by": user, "updated_at": now()})
        row["base_series"] = row.get("base_series") or re.sub(r"\s*\(\d+\)$", "", str(row["series"]))
        self.db.insert("contracts", [row])
        self.audit(user, "create", "contracts", fa, None, None,
                   {k: row[k] for k in ("series", "program_code", "issue_date", "maturity_date", "isin")}, reason)
        if generate:
            self.regenerate_events(user, fa, reason)
        return fa

    def update_contract(self, user: str, fa: int, changes: dict, reason: str) -> list[str]:
        cur = self._row("contracts", fa)
        real = self._update(user, "contracts", fa, changes, cur, reason)
        if set(real) & {"issue_date", "maturity_date", "annual_fee_override", "program_code"}:
            return self.regenerate_events(user, fa, reason)
        return []

    def regenerate_events(self, user: str, fa: int, reason: str) -> list[str]:
        """Bring a contract's fee schedule in line with its dates. Billed fees are never deleted."""
        data = {t: self.db.read(t) for t in ("contracts", "fee_events", "programs", "settings")}
        start = self.schedule_start(data)
        contract = data["contracts"][data["contracts"]["fa_number"] == fa].iloc[0].to_dict()
        wanted = {e["event_id"]: e for e in engine.build_events(contract, data["programs"], start, user, now())}
        existing = data["fee_events"][data["fee_events"]["fa_number"] == fa]
        warnings = []
        for r in existing.itertuples():
            if r.event_id in wanted:
                exp = wanted[r.event_id]["expected_amount"]
                if r.invoice_no is None and abs((r.expected_amount or 0) - exp) > 0.005:
                    self._update(user, "fee_events", r.event_id, {"expected_amount": exp}, r._asdict(), reason)
                continue
            if r.invoice_no:
                msg = (f"FA {fa}: fee dated {r.event_date:%d-%b-%Y} was billed on invoice {r.invoice_no} "
                       f"but no longer fits the contract dates. Kept; please review.")
                warnings.append(msg)
                self.add_issue(user, "Billed fee outside schedule", "fee_events", r.event_id, msg, r.expected_amount)
            else:
                self.db.delete("fee_events", "event_id", r.event_id)
                self.audit(user, "delete", "fee_events", r.event_id, None,
                           {"event_date": r.event_date, "expected_amount": r.expected_amount}, None, reason)
        new = [e for eid, e in wanted.items() if eid not in set(existing["event_id"])]
        if new:
            self.db.insert("fee_events", new)
            self.audit(user, "create", "fee_events", fa, None, None,
                       {"fees_added": len(new), "dates": [e["event_date"] for e in new]}, reason)
        return warnings

    def write_off(self, user: str, event_id: str, wo_date: date, reason: str) -> None:
        cur = self._row("fee_events", event_id)
        if cur.get("invoice_no"):
            raise ValueError("This fee is on an invoice. Reverse the invoice line first.")
        self._update(user, "fee_events", event_id,
                     {"written_off": True, "write_off_date": wo_date, "write_off_reason": reason}, cur, reason,
                     action="write_off")

    def reinstate(self, user: str, event_id: str, reason: str) -> None:
        cur = self._row("fee_events", event_id)
        self._update(user, "fee_events", event_id,
                     {"written_off": False, "write_off_date": None, "write_off_reason": None}, cur, reason,
                     action="reinstate")

    # ------------------------------------------------------------------ invoices
    def preview_invoice(self, data_bytes: bytes, data: dict | None = None) -> MatchResult:
        inv = parse_invoice(data_bytes)
        return self.match(inv, data)

    def match(self, inv: ParsedInvoice, data: dict | None = None) -> MatchResult:
        data = data or self.load()
        return match_invoice(inv, data["contracts"], data["fee_events"], data["invoices"], data["programs"],
                             self.schedule_start(data))

    def post_invoice(self, user: str, result: MatchResult, *, file_name: str | None = None,
                     file_bytes: bytes | None = None, overrides: dict[int, str | None] | None = None,
                     approval_date: date | None = None, approved_by: str | None = None,
                     payment_date: date | None = None, payment_ref: str | None = None,
                     notes: str | None = None, source: str = "pdf", reason: str = "Invoice posted") -> dict:
        inv = result.invoice
        if result.blocking:
            raise ValueError(result.blocking[0].detail)
        overrides = overrides or {}
        events = self.db.read("fee_events").set_index("event_id")
        path = self.files.save(f"invoices/{inv.invoice_no}", file_name or f"{inv.invoice_no}.pdf", file_bytes) \
            if file_bytes else None

        line_rows, links, used = [], [], set()
        counts = Counter()
        for lm in result.lines:
            status, eid, fa, notes_l = lm.status, lm.event_id, lm.fa_number, list(lm.notes)
            if lm.line_no in overrides:
                chosen = overrides[lm.line_no]
                if chosen != lm.event_id:
                    status, eid = (MANUAL, chosen) if chosen else (RED, None)
                    fa = int(events.loc[chosen, "fa_number"]) if chosen else lm.fa_number
                    notes_l.append(f"Manually {'matched to ' + chosen if chosen else 'left unmatched'} by {user}.")
                elif status == RED and chosen:
                    status = MANUAL
                    notes_l.append(f"Accepted by {user} despite: {'; '.join(lm.notes)}")
            if status == RED:
                eid = None  # red lines never count as payment until a person resolves them
            if eid and eid in used:
                status, eid = RED, None
                notes_l.append("Duplicate selection on this invoice; left unmatched.")
            if eid:
                used.add(eid)
                links.append((eid, lm))
            counts[status] += 1
            line_id = f"{inv.invoice_no}-{lm.line_no:03d}"
            line_rows.append({
                "line_id": line_id, "invoice_no": inv.invoice_no, "line_no": lm.line_no, "series": lm.series,
                "maturity_date": lm.maturity_date, "action_date": lm.action_date, "amount": lm.amount,
                "currency": lm.currency, "fa_number": fa, "event_id": eid, "match_status": status,
                "match_notes": " | ".join(notes_l) or None,
                "resolved_by": user if status == MANUAL else None, "resolved_at": now() if status == MANUAL else None,
            })

        self.db.insert("invoices", [{
            "invoice_no": inv.invoice_no, "program_code": inv.program_code, "invoice_date": inv.invoice_date,
            "arrears_start": inv.arrears_start, "arrears_end": inv.arrears_end,
            "advance_start": inv.advance_start, "advance_end": inv.advance_end,
            "fee_total": inv.fee_total, "lines_total": inv.lines_total, "line_count": len(inv.lines),
            "currency": inv.currency, "approval_date": approval_date, "approved_by": approved_by,
            "payment_date": payment_date, "payment_ref": payment_ref, "file_name": file_name, "file_path": path,
            "file_hash": file_hash(file_bytes) if file_bytes else None, "source": source, "notes": notes,
            "created_by": user, "created_at": now(), "updated_by": user, "updated_at": now(),
        }])
        self.db.insert("invoice_lines", line_rows)
        for eid, lm in links:
            self.db.update("fee_events", eid, {"invoice_no": inv.invoice_no,
                                               "line_id": f"{inv.invoice_no}-{lm.line_no:03d}",
                                               "billed_amount": lm.amount, "updated_by": user, "updated_at": now()})
        self.audit(user, "post_invoice", "invoices", inv.invoice_no, None, None, {
            "total": inv.fee_total, "lines": len(inv.lines), "green": counts[GREEN], "yellow": counts[YELLOW],
            "red": counts[RED], "manual": counts[MANUAL], "prior": counts[PRIOR],
            "approval_date": approval_date, "payment_date": payment_date, "file": path,
        }, reason)
        for r in line_rows:
            if r["match_status"] == RED:
                self.add_issue(user, "Unmatched invoice line", "invoice_lines", r["line_id"],
                               f"Invoice {inv.invoice_no} line {r['line_no']}: series {r['series']} "
                               f"{r['action_date']:%d-%b-%y} ${r['amount']:,.0f}. {r['match_notes'] or ''}",
                               r["amount"])
        return dict(counts)

    def update_invoice(self, user: str, invoice_no: str, changes: dict, reason: str) -> dict:
        cur = self._row("invoices", invoice_no)
        return self._update(user, "invoices", invoice_no, changes, cur, reason)

    def resolve_line(self, user: str, line_id: str, event_id: str | None, reason: str) -> None:
        line = self._row("invoice_lines", line_id)
        old_eid = line.get("event_id")
        if event_id:
            ev = self._row("fee_events", event_id)
            if ev.get("invoice_no") and ev["invoice_no"] != line["invoice_no"]:
                raise ValueError(f"That fee is already billed on invoice {ev['invoice_no']}.")
        if old_eid and old_eid != event_id:
            oe = self._row("fee_events", old_eid)
            self._update(user, "fee_events", old_eid, {"invoice_no": None, "line_id": None, "billed_amount": None},
                         oe, reason, action="unlink")
        if event_id:
            ev = self._row("fee_events", event_id)
            self._update(user, "fee_events", event_id, {"invoice_no": line["invoice_no"], "line_id": line_id,
                                                        "billed_amount": line["amount"]}, ev, reason, action="link")
        self._update(user, "invoice_lines", line_id, {
            "event_id": event_id, "fa_number": int(self._row("fee_events", event_id)["fa_number"]) if event_id else line["fa_number"],
            "match_status": MANUAL if event_id else RED, "resolved_by": user, "resolved_at": now(),
        }, line, reason, action="resolve_line")
        issues = self.db.read("data_issues")
        for r in issues[(issues["entity_id"] == line_id) & (issues["status"] == "Open")].itertuples():
            self.resolve_issue(user, r.issue_id, reason)

    def reverse_invoice(self, user: str, invoice_no: str, reason: str) -> None:
        inv = self._row("invoices", invoice_no)
        lines = self.db.read("invoice_lines")
        lines = lines[lines["invoice_no"] == invoice_no]
        ev = self.db.read("fee_events")
        for r in ev[ev["invoice_no"] == invoice_no].itertuples():
            self.db.update("fee_events", r.event_id, {"invoice_no": None, "line_id": None, "billed_amount": None,
                                                      "updated_by": user, "updated_at": now()})
        self.db.delete("invoice_lines", "invoice_no", invoice_no)
        self.db.delete("invoices", "invoice_no", invoice_no)
        self.audit(user, "reverse_invoice", "invoices", invoice_no, None,
                   {"invoice": inv, "lines": lines.to_dict("records")}, None, reason)

    # ------------------------------------------------------------------ FA register sync
    def register_diff(self, register: pd.DataFrame, data: dict | None = None) -> dict[str, pd.DataFrame]:
        data = data or self.load()
        c = data["contracts"].set_index("fa_number")
        start = self.schedule_start(data)
        reg = register.copy()
        # Only contracts that can still carry a fee inside the tracking window matter.
        reg = reg[reg["issue_date"].notna()]
        new = reg[~reg["fa_number"].isin(c.index) & reg["maturity_date"].notna()]
        new = new[new["maturity_date"] > start]
        changed = []
        for r in reg[reg["fa_number"].isin(c.index)].itertuples():
            cur = c.loc[r.fa_number]
            for fld in ("issue_date", "maturity_date", "isin", "program_code"):
                newv, oldv = getattr(r, fld), cur[fld]
                if _none(newv) is not None and _norm(newv) != _norm(oldv):
                    changed.append({"fa_number": r.fa_number, "series": cur["series"], "field": fld,
                                    "current": oldv, "register": newv})
        return {"new": new.reset_index(drop=True), "changed": pd.DataFrame(changed)}

    def apply_register(self, user: str, register: pd.DataFrame, new_fas: list[int], changes: list[dict],
                       source_doc: str | None, reason: str) -> list[str]:
        warnings = []
        for r in register[register["fa_number"].isin(new_fas)].to_dict("records"):
            if r["maturity_date"] is None:
                warnings.append(f"FA {r['fa_number']} skipped: no maturity date in the register.")
                continue
            self.add_contract(user, {
                "fa_number": r["fa_number"], "program_code": r["program_code"], "series": r["series"],
                "base_series": r["base_series"], "reopening_of": _none(r.get("reopening_of")),
                "isin": r["isin"], "issue_date": r["issue_date"], "maturity_date": r["maturity_date"],
                "principal": r["principal"], "currency": r["currency"], "source": "FA register",
                "source_document": source_doc, "notes": r.get("register_notes"),
            }, reason)
        by_fa: dict[int, dict] = {}
        for ch in changes:
            by_fa.setdefault(int(ch["fa_number"]), {})[ch["field"]] = ch["register"]
        for fa, ch in by_fa.items():
            warnings += self.update_contract(user, fa, ch, reason)
        return warnings

    # ------------------------------------------------------------------ tracker clean-up
    def tracker_document(self) -> tuple[str, bytes] | None:
        """The old tracker workbook saved at the initial load (file name, bytes), or None."""
        docs = self.db.read("documents")
        t = docs[docs["doc_type"] == "tracker"].sort_values("uploaded_at")
        if t.empty:
            return None
        r = t.iloc[-1]
        raw = self.files.load(r["file_path"])
        return (r["file_name"], raw) if raw else None

    def tracker_decisions(self, data: dict | None = None) -> dict[str, dict]:
        """Decision per tracker row ("<FA>:<year>"): {"decision", "note", "by", "at"}."""
        s = (data or {}).get("settings")
        s = self.db.read("settings") if s is None else s
        row = s.loc[s["key"] == "tracker_decisions", "value"]
        return json.loads(row.iloc[0]) if len(row) and row.iloc[0] else {}

    def decide_tracker_rows(self, user: str, changes: dict[str, tuple[str | None, str | None]], reason: str) -> int:
        """Record apply / keep / review (or None to clear) for tracker rows; each change is audited."""
        cur = self.tracker_decisions()
        n = 0
        for fid, (decision, note) in changes.items():
            if decision is not None and decision not in DECISIONS:
                raise ValueError(f"Unknown decision {decision!r}")
            old = cur.get(fid, {})
            if old.get("decision") == decision and (old.get("note") or None) == (note or None):
                continue
            if decision is None:
                cur.pop(fid, None)
            else:
                cur[fid] = {"decision": decision, "note": note or None, "by": user, "at": now().isoformat()}
            self.audit(user, "decide", "tracker_row", fid, "decision", old.get("decision"), decision, note or reason)
            n += 1
        if n:
            self.save_setting(user, "tracker_decisions", to_json(cur), reason, summary={"rows_decided": len(cur)})
        return n

    # ------------------------------------------------------------------ month-end
    def create_snapshot(self, user: str, as_of: date, report_bytes: bytes | None, summary: dict) -> str:
        data = self.load()
        lg = engine.ledger(data["fee_events"], data["contracts"], data["invoices"], data["programs"], as_of)
        out = lg.frame[lg.frame["outstanding"]]
        sid = f"{as_of.isoformat()}-{uuid.uuid4().hex[:6]}"
        path = self.files.save("month_end", f"Citi_accrual_{as_of.isoformat()}.xlsx", report_bytes) if report_bytes else None
        self.db.insert("snapshots", [{
            "snapshot_id": sid, "as_of": as_of, "total_outstanding": float(out["expected_amount"].sum()),
            "event_count": int(len(out)), "summary_json": to_json(summary), "file_path": path,
            "created_by": user, "created_at": now(),
        }])
        self.db.insert("snapshot_lines", [{
            "snapshot_id": sid, "event_id": r.event_id, "fa_number": r.fa_number, "series": r.series,
            "program_code": r.program_code, "event_date": r.event_date, "amount": r.expected_amount,
            "status": r.status, "invoice_no": r.invoice_no,
        } for r in out.itertuples()])
        self.audit(user, "month_end_snapshot", "snapshots", sid, None, None,
                   {"as_of": as_of, "total": float(out["expected_amount"].sum())}, "Month-end accrual finalized")
        return sid

    # ------------------------------------------------------------------ revert
    def revert(self, user: str, audit_id: str, reason: str) -> str:
        log = self.db.read("audit_log")
        row = log[log["audit_id"] == audit_id]
        if row.empty:
            raise KeyError("Audit entry not found")
        a = row.iloc[0]
        if a["action"] not in ("update", "write_off", "reinstate") or not a["field"]:
            raise ValueError("Only field changes can be reverted here. Use 'Reverse invoice' for postings.")
        table, key, fld = a["entity"], a["entity_id"], a["field"]
        key_typed = int(key) if coltype(table, _key(table)) in ("BIGINT", "INT") else key
        cur = self._row(table, key_typed)
        old = _parse_typed(table, fld, a["old_value"])
        if table == "contracts":
            self.update_contract(user, key_typed, {fld: old}, f"Revert: {reason}")
        else:
            self._update(user, table, key_typed, {fld: old}, cur, f"Revert: {reason}", action="update")
        return f"{table} {key} {fld} set back to {a['old_value']}"

    # ------------------------------------------------------------------ initial load
    def initial_load(self, user: str, files: list[tuple[str, bytes]], as_of: date | None = None) -> dict:
        """One-time load from the existing folder: FA register, tracker workbook and every Citi invoice."""
        if not self.is_empty():
            raise ValueError("Data already loaded. Use Setup > Reset only if you intend to start over.")
        self.ensure_defaults(user)
        classified = [classify_file(n, b) for n, b in files]
        kinds = Counter(f.kind for f in classified)
        report: dict = {"files": pd.DataFrame([{"file": f.name, "type": f.kind, "note": f.detail} for f in classified])}
        reg_f = next((f for f in classified if f.kind == "fa_register"), None)
        trk_f = next((f for f in classified if f.kind == "tracker"), None)
        tracker = parse_tracker(trk_f.data, trk_f.name) if trk_f else pd.DataFrame()
        reason = "Initial load from existing documents"
        start = self.settings.schedule_start

        # 1) contracts --------------------------------------------------------------------------
        if reg_f:
            reg = parse_fa_register(reg_f.data, reg_f.name)
            doc = self.files.save("registers", reg_f.name, reg_f.data)
            self._doc(user, "fa_register", reg_f, doc)
            keep = reg[reg["issue_date"].notna()]
            no_mat = keep[keep["maturity_date"].isna()]
            for r in no_mat.itertuples():
                self.add_issue(user, "Register data", "contracts", r.fa_number,
                               f"FA {r.fa_number} ({r.series_raw}) has no maturity date in the FA register; not loaded.")
            rows = []
            for r in keep[keep["maturity_date"].notna()].to_dict("records"):
                rows.append({
                    "fa_number": r["fa_number"], "program_code": r["program_code"], "series": r["series"],
                    "base_series": r["base_series"], "reopening_of": _none(r["reopening_of"]), "isin": r["isin"],
                    "trade_date": None, "issue_date": r["issue_date"], "maturity_date": r["maturity_date"],
                    "annual_fee_override": None, "principal": r["principal"], "currency": r["currency"],
                    "source": "FA register", "source_document": doc, "notes": r["register_notes"],
                    "created_by": user, "created_at": now(), "updated_by": user, "updated_at": now(),
                })
            self.db.insert("contracts", rows)
            self.audit(user, "create", "contracts", "initial_load", None, None, {"contracts": len(rows)}, reason)
        elif not tracker.empty:
            rows = []
            for r in tracker.sort_values("tab_year", ascending=False).drop_duplicates("fa_number").to_dict("records"):
                rows.append({"fa_number": r["fa_number"], "program_code": "REPACK6883", "series": r["series"],
                             "base_series": re.sub(r"\s*\(\d+\)$", "", r["series"]), "issue_date": r["issue_date"],
                             "maturity_date": r["maturity_date"], "source": "Tracker", "created_by": user,
                             "created_at": now(), "updated_by": user, "updated_at": now()})
            self.db.insert("contracts", rows)
        else:
            raise ValueError("The folder needs the FA register or the accrual tracker workbook.")

        contracts = self.db.read("contracts")
        # tracker vs register conflicts (register is the system of record)
        if not tracker.empty and reg_f:
            cidx = contracts.set_index("fa_number")
            seen = set()
            for r in tracker.itertuples():
                if r.fa_number in seen:
                    continue
                seen.add(r.fa_number)
                if r.fa_number not in cidx.index:
                    self.add_issue(user, "Tracker vs register", "contracts", r.fa_number,
                                   f"FA {r.fa_number} ({r.series}) is in the tracker but not in the FA register.")
                    continue
                for fld, label in (("issue_date", "issue date"), ("maturity_date", "maturity date")):
                    tv, rv = getattr(r, fld), cidx.loc[r.fa_number, fld]
                    if tv and rv and tv != rv:
                        self.add_issue(user, "Tracker vs register", "contracts", r.fa_number,
                                       f"FA {r.fa_number} ({r.series}): tracker {label} {tv:%d-%b-%Y}, "
                                       f"FA register {rv:%d-%b-%Y}. Register date used.")

        # 2) fee schedule -----------------------------------------------------------------------
        programs = self.db.read("programs")
        events = []
        for c in contracts.to_dict("records"):
            events += engine.build_events(c, programs, start, user, now())
        self.db.insert("fee_events", events)
        self.audit(user, "create", "fee_events", "initial_load", None, None, {"fees": len(events)}, reason)

        # 3) invoices from PDFs, in date order ----------------------------------------------------
        tracker_dates = {}
        if not tracker.empty:
            for inv_no, grp in tracker[tracker["invoice_no"].notna()].groupby("invoice_no"):
                d = grp["approved_date"].dropna()
                tracker_dates[inv_no] = Counter(d).most_common(1)[0][0] if len(d) else None
        parsed = []
        for f in classified:
            if f.kind == "invoice":
                try:
                    parsed.append((parse_invoice(f.data), f))
                except Exception as exc:
                    self.add_issue(user, "Unreadable invoice", "documents", f.name, str(exc))
        parsed.sort(key=lambda p: (p[0].invoice_date or date.min, p[0].invoice_no))
        posted = []
        for inv, f in parsed:
            result = self.match(inv)
            tdate = tracker_dates.get(inv.invoice_no)
            approval = inv.approval_date or tdate
            payment = tdate or inv.approval_date
            note = ("Loaded at initial setup. Payment date assumed from the tracker's 'Date Approved for Payment'"
                    if tdate else "Loaded at initial setup. Payment date assumed from the 'okay to pay' stamp"
                    if inv.approval_date else "Loaded at initial setup. No approval or payment date available")
            counts = self.post_invoice(user, result, file_name=f.name, file_bytes=f.data, approval_date=approval,
                                       approved_by=user if approval else None, payment_date=payment, notes=note,
                                       reason=reason)
            posted.append({"invoice": inv.invoice_no, "program": inv.program_code, "date": inv.invoice_date,
                           "total": inv.fee_total, "lines": len(inv.lines), **counts})

        # 4) tracker invoices with no PDF in the folder ------------------------------------------
        pdf_nos = {p[0].invoice_no for p in parsed}
        if not tracker.empty:
            missing = sorted(set(tracker["invoice_no"].dropna()) - pdf_nos)
            for inv_no in missing:
                posted.append(self._load_tracker_only_invoice(user, inv_no, tracker, tracker_dates.get(inv_no), reason))
        report["invoices"] = pd.DataFrame(posted).fillna(0)
        for col in ("green", "yellow", "red", "manual", "prior"):
            if col in report["invoices"]:
                report["invoices"][col] = report["invoices"][col].astype(int)

        # 5) cross-check tracker recording against what the PDFs billed --------------------------
        if not tracker.empty:
            lines = self.db.read("invoice_lines")
            for inv_no in pdf_nos & set(tracker["invoice_no"].dropna()):
                il = lines[lines["invoice_no"] == inv_no]
                billed_all = set(il["fa_number"].dropna().astype(int))
                billed = set(il.loc[il["match_status"] != PRIOR, "fa_number"].dropna().astype(int))
                recorded = set(tracker.loc[tracker["invoice_no"] == inv_no, "fa_number"].astype(int))
                cidx = contracts.set_index("fa_number")["series"]
                for fa in sorted(billed - recorded):
                    self.add_issue(user, "Tracker recording error", "invoices", inv_no,
                                   f"Invoice {inv_no} billed FA {fa} ({cidx.get(fa)}) but the tracker never recorded it, "
                                   f"so the tracker still shows it as owed.", 3000.0)
                for fa in sorted(recorded - billed_all):
                    self.add_issue(user, "Tracker recording error", "invoices", inv_no,
                                   f"Tracker marks FA {fa} ({cidx.get(fa)}) paid on invoice {inv_no}, "
                                   f"but that invoice does not bill it.", 3000.0)

        # 6) opening balance bridge ----------------------------------------------------------------
        if trk_f:
            as_of = as_of or _as_of_from_name(trk_f.name) or date.today()
            report["bridge"], report["bridge_summary"] = self._bridge(user, tracker, tracker_summary_total(trk_f.data), as_of)
            # Kept so the month-end page can always show why the tracker differed from the tool.
            self.save_setting(user, "opening_bridge",
                              to_json({"summary": report["bridge_summary"], "rows": report["bridge"].to_dict("records")}),
                              reason, summary={"items": len(report["bridge"]),
                                               "tracker_total": report["bridge_summary"]["tracker_total"]})
            self._doc(user, "tracker", trk_f, self.files.save("registers", trk_f.name, trk_f.data))
        report["kinds"] = dict(kinds)
        self.audit(user, "initial_load", "system", "initial_load", None, None,
                   {"files": len(files), **dict(kinds)}, reason)
        return report

    def _doc(self, user, doc_type, f: ClassifiedFile, path):
        self.db.insert("documents", [{"doc_id": uuid.uuid4().hex, "doc_type": doc_type, "file_name": f.name,
                                      "file_path": path, "file_hash": file_hash(f.data), "uploaded_by": user,
                                      "uploaded_at": now(), "notes": None}])

    def _load_tracker_only_invoice(self, user, inv_no, tracker, tdate, reason) -> dict:
        rows = tracker[tracker["invoice_no"] == inv_no]
        ev = self.db.read("fee_events")
        contracts = self.db.read("contracts").set_index("fa_number")
        y, q = quarter_of(tdate) if tdate else (None, None)
        if tdate:  # Citi bills the previous quarter in arrears
            y, q = (y - 1, 4) if q == 1 else (y, q - 1)
            a_start, a_end = quarter_bounds(y, q)
        else:
            a_start = a_end = None
        lines = []
        from .parsers import InvoiceLine
        for i, r in enumerate(rows.itertuples(), start=1):
            fa = int(r.fa_number)
            cand = ev[(ev["fa_number"] == fa)]
            if a_start:
                cand = cand[(cand["event_date"] >= a_start) & (cand["event_date"] <= a_end)]
            d = cand["event_date"].iloc[0] if len(cand) else (r.issue_date or date.today())
            lines.append(InvoiceLine(i, contracts.loc[fa, "base_series"] if fa in contracts.index else r.series,
                                     contracts.loc[fa, "maturity_date"] if fa in contracts.index else r.maturity_date,
                                     d, float(r.amount or 0), "USD"))
        inv = ParsedInvoice(inv_no, "REPACK6883", "Reconstructed from tracker (no PDF on file)", None,
                            a_start, a_end, None, None, round(sum(l.amount for l in lines), 2), "USD", lines)
        inv.invoice_date = tdate
        result = self.match(inv)
        for lm in result.lines:
            if lm.status == GREEN:
                lm.status = YELLOW
            lm.notes.append("No PDF on file; line reconstructed from the tracker.")
        counts = self.post_invoice(user, result, approval_date=tdate, approved_by=user if tdate else None,
                                   payment_date=tdate, source="tracker",
                                   notes="No PDF in the folder. Reconstructed from tracker rows.", reason=reason)
        self.add_issue(user, "Missing source document", "invoices", inv_no,
                       f"Invoice {inv_no} is referenced by the tracker but its PDF was not in the folder. "
                       f"Upload it on the Invoices page to attach it.")
        return {"invoice": inv_no, "program": "REPACK6883", "date": tdate, "total": inv.fee_total,
                "lines": len(lines), **counts}

    def _bridge(self, user, tracker: pd.DataFrame, tracker_total: float | None, as_of: date):
        data = self.load()
        lg = engine.ledger(data["fee_events"], data["contracts"], data["invoices"], data["programs"], as_of).frame
        lg = lg[lg["include_in_accrual"]]
        contracts = data["contracts"].set_index("fa_number")
        recs = []
        t = tracker.copy()
        t["owed"] = t.apply(lambda r: (r["amount"] or 0) if not r["paid"] else 0.0, axis=1)
        keys = set(zip(t["fa_number"], t["tab_year"])) | set(zip(lg.loc[lg["outstanding"], "fa_number"],
                                                                 lg.loc[lg["outstanding"], "fee_year"]))
        for fa, yr in sorted(keys):
            tr = t[(t["fa_number"] == fa) & (t["tab_year"] == yr)]
            trk_owed = float(tr["owed"].sum())
            ev = lg[(lg["fa_number"] == fa) & (lg["fee_year"] == yr)]
            eng_owed = float(ev.loc[ev["outstanding"], "expected_amount"].sum())
            diff = eng_owed - trk_owed
            if abs(diff) < 0.005:
                continue
            series = contracts.loc[fa, "series"] if fa in contracts.index else (tr["series"].iloc[0] if len(tr) else "")
            mat = contracts.loc[fa, "maturity_date"] if fa in contracts.index else None
            issue = contracts.loc[fa, "issue_date"] if fa in contracts.index else None
            if diff < 0:
                due = [e for e in ev.itertuples() if e.event_date <= as_of]
                paid = [e for e in due if e.status == engine.STATUS_PAID]
                anniv = add_years(issue, yr - issue.year) if issue else None
                if paid:
                    why = f"Fee due {paid[0].event_date:%d-%b-%Y} was paid on invoice {paid[0].invoice_no}; tracker {yr} tab still shows it owed"
                elif not len(ev) and anniv and mat and anniv >= mat:
                    why = f"Matures {mat:%d-%b-%Y}, on/before the {anniv:%d-%b-%Y} anniversary, so no {yr} fee is due"
                elif len(ev) and all(e.event_date > as_of for e in ev.itertuples()):
                    why = f"{yr} fee is not due until {ev['event_date'].min():%d-%b-%Y}"
                else:
                    why = f"No {yr} fee is due under the fee schedule"
                cat = "Overstated in tracker"
            else:
                open_ev = ev[ev["outstanding"]]
                d = open_ev["event_date"].min()
                why = f"Fee due {d:%d-%b-%Y} has not been invoiced or paid, but the tracker {yr} tab does not accrue it"
                cat = "Understated in tracker"
            recs.append({"fa_number": int(fa), "series": series, "fee_year": int(yr), "tracker_owed": trk_owed,
                         "tool_owed": eng_owed, "difference": diff, "category": cat, "explanation": why})
        bridge = pd.DataFrame(recs)
        tool_total = float(lg.loc[lg["outstanding"], "expected_amount"].sum())
        summary = {"as_of": as_of, "tracker_total": tracker_total,
                   "tracker_rows_total": float(t["owed"].sum()), "tool_total": tool_total,
                   "difference": tool_total - (tracker_total if tracker_total is not None else float(t["owed"].sum())),
                   "explained": float(bridge["difference"].sum()) if len(bridge) else 0.0}
        for r in recs:
            self.add_issue(user, "Opening balance reconciliation", "contracts", r["fa_number"],
                           f"{r['series']} (FA {r['fa_number']}), {r['fee_year']}: {r['explanation']}.", r["difference"])
        return bridge, summary


def _as_of_from_name(name: str) -> date | None:
    m = re.search(r"(\d{1,2})-(\d{1,2})-(\d{2,4})", name)
    if not m:
        return None
    mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    y = y + 2000 if y < 100 else y
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _key(table: str) -> str:
    from .db import KEYS
    return KEYS[table]


def _norm(v):
    v = _none(v)
    if isinstance(v, pd.Timestamp):
        v = v.to_pydatetime()
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if hasattr(v, "item"):
        return _norm(v.item())
    return v


def _str(v):
    v = _norm(v)
    return None if v is None else str(v)


def _parse_typed(table: str, col: str, s):
    if s is None:
        return None
    t = coltype(table, col)
    if t == "DATE":
        return parse_date(s)
    if t == "DOUBLE":
        return float(s)
    if t in ("INT", "BIGINT"):
        return int(float(s))
    if t == "BOOLEAN":
        return str(s).lower() in ("true", "1")
    return s
