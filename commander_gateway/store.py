from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
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
        with self._lock:
            self._atomic_write(
                self.workflows_dir / f"{workflow_id}.json",
                canonical_json_bytes(record),
            )

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
        with self._lock:
            for path in sorted(self.workflows_dir.glob("*.json")):
                try:
                    record = json.loads(path.read_bytes())
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise GatewayError(
                        "WORKFLOW_CORRUPT", "Gateway workflow record is corrupt", 500
                    ) from exc
                if not isinstance(record, dict):
                    raise GatewayError(
                        "WORKFLOW_CORRUPT", "Gateway workflow record is corrupt", 500
                    )
                if record.get("request_key") == request_key:
                    return path.stem, record
        return None

    def save_idempotency(self, digest: str, record: dict) -> None:
        digest = self._safe_component(digest)
        with self._lock:
            self._atomic_write(
                self.idempotency_dir / f"{digest}.json",
                canonical_json_bytes(record),
            )

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
        with self._lock:
            for path in sorted(self.idempotency_dir.glob("*.json")):
                try:
                    record = json.loads(path.read_bytes())
                except json.JSONDecodeError as exc:
                    raise GatewayError(
                        "IDEMPOTENCY_CORRUPT", "idempotency record is corrupt", 500
                    ) from exc
                if not isinstance(record, dict):
                    raise GatewayError(
                        "IDEMPOTENCY_CORRUPT", "idempotency record is corrupt", 500
                    )
                if record.get("request_key") == request_key:
                    return path.stem, record
        return None

    def list_idempotency(self) -> list[str]:
        return sorted(path.stem for path in self.idempotency_dir.glob("*.json"))
