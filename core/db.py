"""Storage backends.

Production: Delta tables in a Unity Catalog schema, reached through a Databricks SQL warehouse
using the app's service principal (credentials are injected by Databricks Apps).
Local development / tests: a SQLite file with the identical logical schema.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date, datetime
from typing import Any, Iterable

import pandas as pd

from .config import Settings

# Logical schema: table -> list of (column, type). Types are Databricks SQL types.
SCHEMA: dict[str, list[tuple[str, str]]] = {
    "programs": [
        ("program_code", "STRING"), ("program_name", "STRING"), ("short_name", "STRING"),
        ("annual_fee", "DOUBLE"), ("include_in_accrual", "BOOLEAN"),
        ("updated_by", "STRING"), ("updated_at", "TIMESTAMP"),
    ],
    "settings": [("key", "STRING"), ("value", "STRING"), ("updated_by", "STRING"), ("updated_at", "TIMESTAMP")],
    "contracts": [
        ("fa_number", "BIGINT"), ("program_code", "STRING"), ("series", "STRING"), ("base_series", "STRING"),
        ("reopening_of", "BIGINT"), ("isin", "STRING"), ("trade_date", "DATE"), ("issue_date", "DATE"),
        ("maturity_date", "DATE"), ("annual_fee_override", "DOUBLE"), ("principal", "STRING"),
        ("currency", "STRING"), ("source", "STRING"), ("source_document", "STRING"), ("notes", "STRING"),
        ("created_by", "STRING"), ("created_at", "TIMESTAMP"), ("updated_by", "STRING"), ("updated_at", "TIMESTAMP"),
    ],
    "fee_events": [
        ("event_id", "STRING"), ("fa_number", "BIGINT"), ("program_code", "STRING"), ("event_date", "DATE"),
        ("period_no", "INT"), ("expected_amount", "DOUBLE"), ("invoice_no", "STRING"), ("line_id", "STRING"),
        ("billed_amount", "DOUBLE"), ("written_off", "BOOLEAN"), ("write_off_date", "DATE"),
        ("write_off_reason", "STRING"), ("notes", "STRING"), ("updated_by", "STRING"), ("updated_at", "TIMESTAMP"),
    ],
    "invoices": [
        ("invoice_no", "STRING"), ("program_code", "STRING"), ("invoice_date", "DATE"),
        ("arrears_start", "DATE"), ("arrears_end", "DATE"), ("advance_start", "DATE"), ("advance_end", "DATE"),
        ("fee_total", "DOUBLE"), ("lines_total", "DOUBLE"), ("line_count", "INT"), ("currency", "STRING"),
        ("approval_date", "DATE"), ("approved_by", "STRING"), ("payment_date", "DATE"), ("payment_ref", "STRING"),
        ("file_name", "STRING"), ("file_path", "STRING"), ("file_hash", "STRING"), ("source", "STRING"),
        ("notes", "STRING"), ("created_by", "STRING"), ("created_at", "TIMESTAMP"),
        ("updated_by", "STRING"), ("updated_at", "TIMESTAMP"),
    ],
    "invoice_lines": [
        ("line_id", "STRING"), ("invoice_no", "STRING"), ("line_no", "INT"), ("series", "STRING"),
        ("maturity_date", "DATE"), ("action_date", "DATE"), ("amount", "DOUBLE"), ("currency", "STRING"),
        ("fa_number", "BIGINT"), ("event_id", "STRING"), ("match_status", "STRING"), ("match_notes", "STRING"),
        ("resolved_by", "STRING"), ("resolved_at", "TIMESTAMP"),
    ],
    "data_issues": [
        ("issue_id", "STRING"), ("category", "STRING"), ("entity", "STRING"), ("entity_id", "STRING"),
        ("description", "STRING"), ("amount_impact", "DOUBLE"), ("status", "STRING"),
        ("created_by", "STRING"), ("created_at", "TIMESTAMP"),
        ("resolved_by", "STRING"), ("resolved_at", "TIMESTAMP"), ("resolution", "STRING"),
    ],
    "audit_log": [
        ("audit_id", "STRING"), ("ts", "TIMESTAMP"), ("user_name", "STRING"), ("action", "STRING"),
        ("entity", "STRING"), ("entity_id", "STRING"), ("field", "STRING"),
        ("old_value", "STRING"), ("new_value", "STRING"), ("reason", "STRING"),
    ],
    "snapshots": [
        ("snapshot_id", "STRING"), ("as_of", "DATE"), ("total_outstanding", "DOUBLE"), ("event_count", "INT"),
        ("summary_json", "STRING"), ("file_path", "STRING"), ("created_by", "STRING"), ("created_at", "TIMESTAMP"),
    ],
    "snapshot_lines": [
        ("snapshot_id", "STRING"), ("event_id", "STRING"), ("fa_number", "BIGINT"), ("series", "STRING"),
        ("program_code", "STRING"), ("event_date", "DATE"), ("amount", "DOUBLE"), ("status", "STRING"),
        ("invoice_no", "STRING"),
    ],
    "documents": [
        ("doc_id", "STRING"), ("doc_type", "STRING"), ("file_name", "STRING"), ("file_path", "STRING"),
        ("file_hash", "STRING"), ("uploaded_by", "STRING"), ("uploaded_at", "TIMESTAMP"), ("notes", "STRING"),
    ],
}

KEYS = {
    "programs": "program_code", "settings": "key", "contracts": "fa_number", "fee_events": "event_id",
    "invoices": "invoice_no", "invoice_lines": "line_id", "data_issues": "issue_id", "audit_log": "audit_id",
    "snapshots": "snapshot_id", "documents": "doc_id",
}

_SQLITE_TYPES = {"STRING": "TEXT", "BIGINT": "INTEGER", "INT": "INTEGER", "DOUBLE": "REAL",
                 "DATE": "TEXT", "TIMESTAMP": "TEXT", "BOOLEAN": "INTEGER"}


def coltype(table: str, col: str) -> str:
    for c, t in SCHEMA[table]:
        if c == col:
            return t
    raise KeyError(f"{table}.{col}")


def normalize_frame(table: str, df: pd.DataFrame) -> pd.DataFrame:
    """Give every backend's output identical Python types (date, bool, float, Int64)."""
    for col, typ in SCHEMA[table]:
        if col not in df.columns:
            df[col] = None
        s = df[col]
        if typ == "DATE":
            df[col] = pd.to_datetime(s, errors="coerce").dt.date
            df[col] = df[col].astype(object).where(df[col].notna(), None)
        elif typ == "TIMESTAMP":
            df[col] = pd.to_datetime(s, errors="coerce")
        elif typ == "BOOLEAN":
            df[col] = s.map(lambda v: bool(v) if v is not None and not (isinstance(v, float) and pd.isna(v)) else False)
        elif typ == "DOUBLE":
            df[col] = pd.to_numeric(s, errors="coerce")
        elif typ in ("BIGINT", "INT"):
            df[col] = pd.to_numeric(s, errors="coerce").astype("Int64")
        else:
            df[col] = s.astype(object).where(s.notna(), None)
    return df[[c for c, _ in SCHEMA[table]]]


def _clean(v: Any) -> Any:
    """Convert pandas/numpy scalars into plain Python values for DB drivers."""
    if v is None:
        return None
    if isinstance(v, pd.Timestamp):
        return None if pd.isna(v) else v.to_pydatetime()
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(v, "item"):
        return v.item()
    return v


class Backend:
    def table(self, name: str) -> str:
        raise NotImplementedError

    def execute(self, sql: str, params: dict | None = None) -> None:
        raise NotImplementedError

    def query(self, sql: str, params: dict | None = None) -> pd.DataFrame:
        raise NotImplementedError

    def ensure_schema(self) -> None:
        raise NotImplementedError

    # ---- generic helpers built on execute/query --------------------------------------------
    def read(self, table: str) -> pd.DataFrame:
        return normalize_frame(table, self.query(f"SELECT * FROM {self.table(table)}"))

    def insert(self, table: str, rows: Iterable[dict], chunk: int = 40) -> int:
        rows = list(rows)
        if not rows:
            return 0
        cols = [c for c, _ in SCHEMA[table]]
        n = 0
        for start in range(0, len(rows), chunk):
            part = rows[start:start + chunk]
            params, values = {}, []
            for i, row in enumerate(part):
                ph = []
                for c in cols:
                    key = f"p{i}_{c}"
                    params[key] = self.adapt(table, c, row.get(c))
                    ph.append(self.placeholder(table, c, key))
                values.append("(" + ", ".join(ph) + ")")
            sql = f"INSERT INTO {self.table(table)} ({', '.join('`' + c + '`' for c in cols)}) VALUES " + ", ".join(values)
            self.execute(sql, params)
            n += len(part)
        return n

    def update(self, table: str, key_value: Any, changes: dict) -> None:
        if not changes:
            return
        key = KEYS[table]
        params = {f"v_{c}": self.adapt(table, c, v) for c, v in changes.items()}
        params["k"] = self.adapt(table, key, key_value)
        sets = ", ".join(f"`{c}` = {self.placeholder(table, c, 'v_' + c)}" for c in changes)
        self.execute(f"UPDATE {self.table(table)} SET {sets} WHERE `{key}` = {self.placeholder(table, key, 'k')}", params)

    def update_where(self, table: str, where_col: str, where_value: Any, changes: dict) -> None:
        params = {f"v_{c}": self.adapt(table, c, v) for c, v in changes.items()}
        params["w"] = self.adapt(table, where_col, where_value)
        sets = ", ".join(f"`{c}` = {self.placeholder(table, c, 'v_' + c)}" for c in changes)
        self.execute(f"UPDATE {self.table(table)} SET {sets} WHERE `{where_col}` = {self.placeholder(table, where_col, 'w')}", params)

    def delete(self, table: str, col: str, value: Any) -> None:
        self.execute(f"DELETE FROM {self.table(table)} WHERE `{col}` = {self.placeholder(table, col, 'v')}",
                     {"v": self.adapt(table, col, value)})

    def truncate_all(self) -> None:
        for t in SCHEMA:
            self.execute(f"DELETE FROM {self.table(t)}")

    def adapt(self, table: str, col: str, v: Any) -> Any:
        return _clean(v)

    def placeholder(self, table: str, col: str, key: str) -> str:
        return f":{key}"


class SQLiteBackend(Backend):
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)

    def table(self, name: str) -> str:
        return name

    def execute(self, sql: str, params: dict | None = None) -> None:
        with self._lock:
            self._conn.execute(sql, params or {})
            self._conn.commit()

    def query(self, sql: str, params: dict | None = None) -> pd.DataFrame:
        with self._lock:
            return pd.read_sql_query(sql, self._conn, params=params or {})

    def adapt(self, table: str, col: str, v: Any) -> Any:
        v = _clean(v)
        typ = coltype(table, col)
        if v is None:
            return None
        if typ == "DATE":
            return v.isoformat() if isinstance(v, (date, datetime)) else str(v)
        if typ == "TIMESTAMP":
            return v.isoformat(sep=" ") if isinstance(v, datetime) else str(v)
        if typ == "BOOLEAN":
            return int(bool(v))
        return v

    def ensure_schema(self) -> None:
        for t, cols in SCHEMA.items():
            body = ", ".join(f"`{c}` {_SQLITE_TYPES[ty]}" for c, ty in cols)
            self.execute(f"CREATE TABLE IF NOT EXISTS {t} ({body})")


class DatabricksBackend(Backend):
    """Unity Catalog Delta tables via a SQL warehouse, authenticated as the app's service principal."""

    def __init__(self, settings: Settings):
        from databricks import sql as dbsql
        from databricks.sdk.core import Config

        if not settings.warehouse_id:
            raise RuntimeError("DATABRICKS_WAREHOUSE_ID is not set. Add a SQL warehouse resource to the app.")
        self.settings = settings
        self._cfg = Config()
        self._dbsql = dbsql
        self._lock = threading.Lock()
        self._conn = None

    def _connection(self):
        if self._conn is None:
            self._conn = self._dbsql.connect(
                server_hostname=self._cfg.host,
                http_path=f"/sql/1.0/warehouses/{self.settings.warehouse_id}",
                credentials_provider=lambda: self._cfg.authenticate,
            )
        return self._conn

    def _run(self, fn):
        with self._lock:
            try:
                return fn(self._connection())
            except Exception:
                # Connections drop when the warehouse auto-stops; reconnect once.
                try:
                    if self._conn is not None:
                        self._conn.close()
                except Exception:
                    pass
                self._conn = None
                return fn(self._connection())

    def table(self, name: str) -> str:
        return f"`{self.settings.catalog}`.`{self.settings.schema}`.`{name}`"

    def placeholder(self, table: str, col: str, key: str) -> str:
        # Explicit casts keep NULLs and Python ints typed correctly for Delta.
        return f"CAST(:{key} AS {coltype(table, col)})"

    def adapt(self, table: str, col: str, v: Any) -> Any:
        v = _clean(v)
        if v is None:
            return None
        typ = coltype(table, col)
        if typ == "DATE":
            return (v.isoformat() if isinstance(v, (date, datetime)) else str(v))[:10]
        if typ == "TIMESTAMP":
            return v.isoformat(sep=" ") if isinstance(v, datetime) else str(v)
        if typ == "BOOLEAN":
            return "true" if v else "false"
        return str(v) if typ != "STRING" else v

    def execute(self, sql: str, params: dict | None = None) -> None:
        def go(conn):
            with conn.cursor() as cur:
                cur.execute(sql, params or {})
        self._run(go)

    def query(self, sql: str, params: dict | None = None) -> pd.DataFrame:
        def go(conn):
            with conn.cursor() as cur:
                cur.execute(sql, params or {})
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description] if cur.description else []
                return pd.DataFrame([list(r) for r in rows], columns=cols)
        return self._run(go)

    def ensure_schema(self) -> None:
        s = self.settings
        # The schema and volume are normally pre-created by an admin (see sql/schema.sql); the app's
        # service principal may not have CREATE rights at catalog level, so failures here are not fatal.
        for stmt in (f"CREATE SCHEMA IF NOT EXISTS `{s.catalog}`.`{s.schema}`",
                     f"CREATE VOLUME IF NOT EXISTS `{s.catalog}`.`{s.schema}`.`{s.volume}`"):
            try:
                self.execute(stmt)
            except Exception:
                pass
        for t, cols in SCHEMA.items():
            body = ", ".join(f"`{c}` {ty}" for c, ty in cols)
            self.execute(f"CREATE TABLE IF NOT EXISTS {self.table(t)} ({body}) USING DELTA")


def make_backend(settings: Settings) -> Backend:
    if settings.backend == "databricks":
        return DatabricksBackend(settings)
    return SQLiteBackend(settings.sqlite_path)


def to_json(obj: Any) -> str:
    def default(o):
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        if hasattr(o, "item"):
            return o.item()
        return str(o)
    return json.dumps(obj, default=default)
