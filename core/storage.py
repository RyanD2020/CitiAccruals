"""Keeps every uploaded source document so each number in the ledger can be traced to its PDF."""
from __future__ import annotations

import io
import os
import re

from .config import Settings


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip() or "file"


class FileStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = None
        if settings.backend == "databricks":
            from databricks.sdk import WorkspaceClient
            self._client = WorkspaceClient()
        else:
            os.makedirs(settings.local_files_dir, exist_ok=True)

    def save(self, folder: str, name: str, data: bytes) -> str:
        if self._client is not None:
            path = f"{self.settings.volume_root}/{folder}/{_safe(name)}"
            self._client.files.upload(path, io.BytesIO(data), overwrite=True)
            return path
        d = os.path.join(self.settings.local_files_dir, folder)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, _safe(name))
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def load(self, path: str) -> bytes | None:
        if not path:
            return None
        try:
            if self._client is not None:
                return self._client.files.download(path).contents.read()
            with open(path, "rb") as fh:
                return fh.read()
        except Exception:
            return None
