from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from commander_gateway.errors import GatewayError


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


class FileGatewayStore:
    """Single-worker file store using atomic replacement for every record."""

    def __init__(self, state_dir: str | Path) -> None:
        self.state_dir = Path(state_dir)
        self.packages_dir = self.state_dir / "packages"
        self.workflows_dir = self.state_dir / "workflows"
        self.idempotency_dir = self.state_dir / "idempotency"
        for directory in (
            self.packages_dir,
            self.workflows_dir,
            self.idempotency_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.index_path = self.state_dir / "request_index.sqlite3"
        self._initialize_index()

    @contextmanager
    def _index(self):
        connection = sqlite3.connect(self.index_path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize_index(self) -> None:
        # JSON remains the durable source of truth. Migrate old archives once;
        # an interrupted write is recovered by reading only its journaled file.
        with self._lock, self._index() as db:
            db.execute("CREATE TABLE IF NOT EXISTS records (kind TEXT, identifier TEXT, request_key TEXT, PRIMARY KEY(kind, identifier))")
            db.execute("CREATE INDEX IF NOT EXISTS request_keys ON records(kind, request_key)")
            db.execute("CREATE TABLE IF NOT EXISTS pending (kind TEXT, identifier TEXT, PRIMARY KEY(kind, identifier))")
            db.execute("CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY, value TEXT)")
            if db.execute("SELECT value FROM metadata WHERE name='initialized'").fetchone() is None:
                for kind, directory in (("workflow", self.workflows_dir), ("idempotency", self.idempotency_dir)):
                    for path in sorted(directory.glob("*.json")):
                        self._index_file(db, kind, path.stem)
                db.execute("INSERT INTO metadata VALUES ('initialized', '1')")
            for kind, identifier in db.execute("SELECT kind, identifier FROM pending").fetchall():
                self._index_file(db, kind, identifier)
            db.execute("DELETE FROM pending")

    def _index_file(self, db, kind: str, identifier: str) -> None:
        directory = self.workflows_dir if kind == "workflow" else self.idempotency_dir
        path = directory / f"{self._safe_component(identifier)}.json"
        if not path.exists():
            db.execute("DELETE FROM records WHERE kind=? AND identifier=?", (kind, identifier))
            return
        try:
            record = json.loads(path.read_bytes())
            if not isinstance(record, dict):
                raise ValueError("record is not an object")
        except (ValueError, UnicodeDecodeError) as exc:
            raise GatewayError(f"{kind.upper()}_CORRUPT", f"{kind} record is corrupt", 500) from exc
        db.execute("INSERT OR REPLACE INTO records VALUES (?, ?, ?)", (kind, identifier, record.get("request_key")))

    def _save_indexed(self, kind: str, identifier: str, record: dict) -> None:
        directory = self.workflows_dir if kind == "workflow" else self.idempotency_dir
        body = canonical_json_bytes(record)
        with self._lock:
            with self._index() as db:
                previous = db.execute("SELECT request_key FROM records WHERE kind=? AND identifier=?", (kind, identifier)).fetchone()
                changed = previous is None or previous[0] != record.get("request_key")
                if changed:
                    db.execute("INSERT OR IGNORE INTO pending VALUES (?, ?)", (kind, identifier))
            self._atomic_write(directory / f"{identifier}.json", body)
            if changed:
                with self._index() as db:
                    db.execute("INSERT OR REPLACE INTO records VALUES (?, ?, ?)", (kind, identifier, record.get("request_key")))
                    db.execute("DELETE FROM pending WHERE kind=? AND identifier=?", (kind, identifier))

    def _find_indexed(self, kind: str, request_key: str) -> tuple[str, dict] | None:
        with self._lock, self._index() as db:
            # Also recover interrupted writes in this process before a retry.
            for pending_kind, identifier in db.execute("SELECT kind, identifier FROM pending").fetchall():
                self._index_file(db, pending_kind, identifier)
            db.execute("DELETE FROM pending")
            rows = db.execute("SELECT identifier FROM records WHERE kind=? AND request_key=? ORDER BY identifier", (kind, request_key)).fetchall()
            for (identifier,) in rows:
                if kind == "workflow":
                    try:
                        record = self.read_workflow(identifier)
                    except GatewayError as exc:
                        if exc.code != "WORKFLOW_NOT_FOUND":
                            raise
                        record = None
                else:
                    record = self.read_idempotency(identifier)
                if record is None:
                    db.execute("DELETE FROM records WHERE kind=? AND identifier=?", (kind, identifier))
                    continue
                if record.get("request_key") != request_key:
                    raise GatewayError(f"{kind.upper()}_CORRUPT", f"{kind} request key mismatch", 500)
                return identifier, record
        return None

    @staticmethod
    def _safe_component(value: str) -> str:
        if not value or value in {".", ".."} or "/" in value or "\\" in value:
            raise GatewayError("INVALID_IDENTIFIER", "invalid storage identifier", 400)
        return value

    def _atomic_write(self, path: Path, body: bytes) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            if os.name != "nt":
                directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            if temporary.exists():
                temporary.unlink()

    def save_package(self, package: dict) -> tuple[str, str, bytes]:
        body = canonical_json_bytes(package)
        checksum = hashlib.sha256(body).hexdigest()
        package_id = str(uuid.uuid4())
        with self._lock:
            self._atomic_write(self.packages_dir / f"{package_id}.json", body)
            self._atomic_write(
                self.packages_dir / f"{package_id}.sha256", checksum.encode("ascii")
            )
        return package_id, checksum, body

    def read_package(self, package_id: str) -> tuple[bytes, str]:
        package_id = self._safe_component(package_id)
        package_path = self.packages_dir / f"{package_id}.json"
        checksum_path = self.packages_dir / f"{package_id}.sha256"
        try:
            body = package_path.read_bytes()
            expected = checksum_path.read_text(encoding="ascii").strip()
        except FileNotFoundError as exc:
            raise GatewayError(
                "PACKAGE_NOT_FOUND", "Gateway package not found", 404, False
            ) from exc
        actual = hashlib.sha256(body).hexdigest()
        if actual != expected:
            raise GatewayError(
                "PACKAGE_CORRUPT", "Gateway package checksum mismatch", 500, False
            )
        return body, actual

    def read_package_json(self, package_id: str) -> dict:
        body, _ = self.read_package(package_id)
        try:
            value = json.loads(body)
        except json.JSONDecodeError as exc:
            raise GatewayError("PACKAGE_CORRUPT", "Gateway package is invalid JSON", 500) from exc
        if not isinstance(value, dict):
            raise GatewayError("PACKAGE_CORRUPT", "Gateway package is not an object", 500)
        return value

    def save_workflow(self, workflow_id: str, record: dict) -> None:
        workflow_id = self._safe_component(workflow_id)
        self._save_indexed("workflow", workflow_id, record)

    def read_workflow(self, workflow_id: str) -> dict:
        workflow_id = self._safe_component(workflow_id)
        try:
            value = json.loads(
                (self.workflows_dir / f"{workflow_id}.json").read_bytes()
            )
        except FileNotFoundError as exc:
            raise GatewayError("WORKFLOW_NOT_FOUND", "Gateway workflow not found", 404) from exc
        except json.JSONDecodeError as exc:
            raise GatewayError("WORKFLOW_CORRUPT", "Gateway workflow record is corrupt", 500) from exc
        if not isinstance(value, dict):
            raise GatewayError("WORKFLOW_CORRUPT", "Gateway workflow record is corrupt", 500)
        return value

    def find_workflow_by_request_key(self, request_key: str) -> tuple[str, dict] | None:
        return self._find_indexed("workflow", request_key)

    def save_idempotency(self, digest: str, record: dict) -> None:
        digest = self._safe_component(digest)
        self._save_indexed("idempotency", digest, record)

    def read_idempotency(self, digest: str) -> dict | None:
        digest = self._safe_component(digest)
        path = self.idempotency_dir / f"{digest}.json"
        try:
            value = json.loads(path.read_bytes())
        except FileNotFoundError:
            return None
        except json.JSONDecodeError as exc:
            raise GatewayError("IDEMPOTENCY_CORRUPT", "idempotency record is corrupt", 500) from exc
        if not isinstance(value, dict):
            raise GatewayError("IDEMPOTENCY_CORRUPT", "idempotency record is corrupt", 500)
        return value

    def find_idempotency_by_request_key(self, request_key: str) -> tuple[str, dict] | None:
        return self._find_indexed("idempotency", request_key)

    def list_idempotency(self) -> list[str]:
        return sorted(path.stem for path in self.idempotency_dir.glob("*.json"))
