"""Durable, process-safe archive for AMOS demonstration run manifests."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any, Callable


RUN_MANIFEST_SCHEMA_VERSION = "amos.run-manifest.v1"
TERMINAL_STATES = {"completed", "stopped", "error", "failed"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clone(value: Any) -> Any:
    """Round-trip JSON data to reject non-contract Python objects."""
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _safe_alerts(alerts: Any) -> list[dict[str, Any]]:
    """Keep only operator-safe alert fields in the durable record."""
    result: list[dict[str, Any]] = []
    for item in alerts or []:
        if not isinstance(item, dict):
            continue
        row = {
            key: item.get(key)
            for key in ("level", "type", "msg", "time")
            if item.get(key) is not None
        }
        if row:
            result.append(row)
    return result[-100:]


def _safe_checkpoint(item: Any) -> dict[str, Any] | None:
    """Keep checkpoint evidence while excluding arbitrary backend payloads."""
    if not isinstance(item, dict):
        return None
    row = {
        key: item.get(key)
        for key in ("checkpoint_id", "title", "reached_at_sec", "analysis_status")
        if item.get(key) is not None
    }
    submission = item.get("submission")
    if isinstance(submission, dict):
        reference = {
            key: submission.get(key)
            for key in ("workflow_id", "status", "error")
            if submission.get(key) is not None
        }
        if reference:
            row["submission"] = reference
    return row or None


def _blank_manifest(
    run_id: str,
    *,
    scenario_id: str | None = None,
    scenario_name: str | None = None,
    seed: int | None = None,
    mode: str | None = None,
    branch: str | None = None,
    platform_mode: str | None = None,
    agent_backend: str | None = None,
    started_at: str | None = None,
    lifecycle_status: str = "ready",
) -> dict[str, Any]:
    now = _now_iso()
    return {
        "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
        "run_id": run_id,
        "scenario_id": scenario_id,
        "scenario_name": scenario_name,
        "seed": seed,
        "mode": mode,
        "branch": branch,
        "platform_mode": platform_mode,
        "agent_backend": agent_backend,
        "started_at": started_at or now,
        "updated_at": now,
        "director": {
            "status": "unconfigured",
            "actions": [],
            "checkpoints": [],
        },
        "submissions": [],
        "workflow_ids": [],
        "workflow_views": [],
        "faults": {"requested": [], "verified": []},
        "lifecycle": {
            "status": lifecycle_status,
            "final_status": None,
            "finished_at": None,
        },
        "alerts": [],
    }


class RunManifestStore:
    """Persist complete run manifests as versioned JSON in SQLite.

    Indexed scalar columns support safe listing while the JSON document keeps
    the archive contract evolvable. Every mutation is an immediate
    transaction, avoiding lost updates between simulation and HTTP threads.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS run_manifests (
                    run_id TEXT PRIMARY KEY,
                    scenario_id TEXT,
                    seed INTEGER,
                    mode TEXT,
                    branch TEXT,
                    final_status TEXT,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    manifest_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_run_manifests_started
                    ON run_manifests(started_at DESC, run_id DESC);
                CREATE INDEX IF NOT EXISTS idx_run_manifests_scenario
                    ON run_manifests(scenario_id, started_at DESC);
                """
            )

    @staticmethod
    def _row_values(manifest: dict[str, Any]) -> tuple[Any, ...]:
        lifecycle = manifest.get("lifecycle") or {}
        return (
            manifest["run_id"],
            manifest.get("scenario_id"),
            manifest.get("seed"),
            manifest.get("mode"),
            manifest.get("branch"),
            lifecycle.get("final_status"),
            manifest.get("started_at") or _now_iso(),
            manifest.get("updated_at") or _now_iso(),
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False),
        )

    def _write(self, connection: sqlite3.Connection, manifest: dict[str, Any]) -> None:
        connection.execute(
            """
            INSERT INTO run_manifests (
                run_id, scenario_id, seed, mode, branch, final_status,
                started_at, updated_at, manifest_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                scenario_id=excluded.scenario_id,
                seed=excluded.seed,
                mode=excluded.mode,
                branch=excluded.branch,
                final_status=excluded.final_status,
                started_at=excluded.started_at,
                updated_at=excluded.updated_at,
                manifest_json=excluded.manifest_json
            """,
            self._row_values(manifest),
        )

    def _mutate(
        self,
        run_id: str,
        update: Callable[[dict[str, Any]], None],
        *,
        initial: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        key = str(run_id or "").strip()
        if not key:
            raise ValueError("run_id is required")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT manifest_json FROM run_manifests WHERE run_id = ?",
                (key,),
            ).fetchone()
            manifest = json.loads(row["manifest_json"]) if row else deepcopy(initial or _blank_manifest(key))
            update(manifest)
            manifest["schema_version"] = RUN_MANIFEST_SCHEMA_VERSION
            manifest["run_id"] = key
            manifest["updated_at"] = _now_iso()
            manifest = _clone(manifest)
            self._write(connection, manifest)
            connection.commit()
            return deepcopy(manifest)

    def begin_run(
        self,
        context: dict[str, Any],
        *,
        scenario_name: str | None = None,
        mode: str = "integration",
        branch: str | None = None,
        lifecycle_status: str = "ready",
    ) -> dict[str, Any]:
        """Create or replace the archive for a newly allocated run ID."""
        run_id = str(context.get("run_id") or "").strip()
        if not run_id:
            raise ValueError("run_id is required")
        seed = context.get("seed")
        manifest = _blank_manifest(
            run_id,
            scenario_id=str(context.get("scenario_id") or "") or None,
            scenario_name=scenario_name,
            seed=int(seed) if seed is not None else None,
            mode=mode,
            branch=branch,
            platform_mode=str(context.get("platform_mode") or "") or None,
            agent_backend=str(context.get("agent_backend") or "") or None,
            started_at=str(context.get("started_at") or "") or None,
            lifecycle_status=lifecycle_status,
        )
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._write(connection, manifest)
            connection.commit()
        return deepcopy(manifest)

    def record_lifecycle(
        self,
        run_id: str,
        status: str,
        *,
        alerts: Any = None,
    ) -> dict[str, Any]:
        normalized = str(status or "unknown").lower()

        def update(manifest: dict[str, Any]) -> None:
            lifecycle = manifest.setdefault("lifecycle", {})
            lifecycle["status"] = normalized
            if normalized in TERMINAL_STATES:
                lifecycle["final_status"] = normalized
                lifecycle["finished_at"] = lifecycle.get("finished_at") or _now_iso()
            if alerts is not None:
                manifest["alerts"] = _safe_alerts(alerts)

        return self._mutate(run_id, update)

    def record_director_state(self, run_id: str, state: dict[str, Any]) -> dict[str, Any]:
        """Persist the public director ledger without unreached script truth."""
        state = _clone(state)

        def update(manifest: dict[str, Any]) -> None:
            checkpoints = [
                row
                for row in (_safe_checkpoint(item) for item in state.get("reached_checkpoints") or [])
                if row is not None
            ]
            manifest["mode"] = state.get("mode") or manifest.get("mode")
            manifest["branch"] = state.get("branch") or manifest.get("branch")
            manifest["director"] = {
                "status": state.get("director_status"),
                "current_checkpoint": _safe_checkpoint(state.get("current_checkpoint")),
                "actions": deepcopy(state.get("action_log") or []),
                "checkpoints": checkpoints,
                "last_error": state.get("last_error"),
            }
            manifest["faults"] = {
                "requested": deepcopy(state.get("requested_faults") or []),
                "verified": deepcopy(state.get("verified_faults") or []),
            }

        return self._mutate(run_id, update)

    def record_submission(
        self,
        run_id: str,
        *,
        workflow_id: str | None,
        submission: dict[str, Any],
    ) -> dict[str, Any]:
        """Freeze the exact browser-safe submission summary for a run."""
        frozen = _clone(submission)
        workflow_key = str(workflow_id or "").strip() or None
        initial = _blank_manifest(
            run_id,
            scenario_id=str(frozen.get("scenario_id") or "") or None,
            seed=frozen.get("seed") if isinstance(frozen.get("seed"), int) else None,
        )

        def update(manifest: dict[str, Any]) -> None:
            rows = manifest.setdefault("submissions", [])
            record = {
                "submission_id": workflow_key or f"attempt-{len(rows) + 1:04d}",
                "workflow_id": workflow_key,
                "recorded_at": _now_iso(),
                "snapshot": frozen,
            }
            if workflow_key:
                rows[:] = [item for item in rows if item.get("workflow_id") != workflow_key]
                workflow_ids = manifest.setdefault("workflow_ids", [])
                if workflow_key not in workflow_ids:
                    workflow_ids.append(workflow_key)
            rows.append(record)

        return self._mutate(run_id, update, initial=initial)

    def record_workflow_view(self, run_id: str, view: dict[str, Any]) -> dict[str, Any]:
        """Persist only the stable workflow-view v2 acceptance fields."""
        workflow_id = str(view.get("workflow_id") or "").strip()
        if not workflow_id:
            raise ValueError("workflow_id is required")
        record = {
            "schema_version": view.get("schema_version"),
            "workflow_id": workflow_id,
            "status": view.get("status"),
            "terminal": view.get("terminal"),
            "recorded_at": _now_iso(),
            "agents": deepcopy(view.get("agents")),
            "algorithms": deepcopy(view.get("algorithms")),
            "function_points": deepcopy(view.get("function_points")),
            "execution_graph": deepcopy(view.get("execution_graph")),
            "metrics": deepcopy(view.get("metrics")),
            "provenance": deepcopy(view.get("provenance")),
            "result": deepcopy(view.get("result")),
        }
        record = _clone(record)

        def update(manifest: dict[str, Any]) -> None:
            rows = manifest.setdefault("workflow_views", [])
            rows[:] = [item for item in rows if item.get("workflow_id") != workflow_id]
            rows.append(record)
            workflow_ids = manifest.setdefault("workflow_ids", [])
            if workflow_id not in workflow_ids:
                workflow_ids.append(workflow_id)

        return self._mutate(run_id, update)

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT manifest_json FROM run_manifests WHERE run_id = ?",
                (str(run_id),),
            ).fetchone()
        return json.loads(row["manifest_json"]) if row else None

    def list(self, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT manifest_json FROM run_manifests
                ORDER BY started_at DESC, run_id DESC LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        summaries: list[dict[str, Any]] = []
        for row in rows:
            manifest = json.loads(row["manifest_json"])
            lifecycle = manifest.get("lifecycle") or {}
            summaries.append({
                "schema_version": manifest.get("schema_version"),
                "run_id": manifest.get("run_id"),
                "scenario_id": manifest.get("scenario_id"),
                "scenario_name": manifest.get("scenario_name"),
                "seed": manifest.get("seed"),
                "mode": manifest.get("mode"),
                "branch": manifest.get("branch"),
                "started_at": manifest.get("started_at"),
                "updated_at": manifest.get("updated_at"),
                "status": lifecycle.get("status"),
                "final_status": lifecycle.get("final_status"),
                "workflow_count": len(manifest.get("workflow_ids") or []),
            })
        return summaries


def _display(value: Any) -> str:
    return "未上报" if value is None or value == "" else str(value)


def _json_block(value: Any) -> str:
    if value is None or value == [] or value == {}:
        return "未上报"
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)


def render_markdown_report(manifest: dict[str, Any]) -> str:
    """Render an acceptance report without inventing unavailable evidence."""
    lifecycle = manifest.get("lifecycle") or {}
    lines = [
        f"# AMOS 运行验收报告：{_display(manifest.get('run_id'))}",
        "",
        "## 运行信息",
        "",
        "| 字段 | 值 |",
        "| --- | --- |",
    ]
    metadata = (
        ("场景", manifest.get("scenario_name") or manifest.get("scenario_id")),
        ("场景 ID", manifest.get("scenario_id")),
        ("随机种子", manifest.get("seed")),
        ("导演模式", manifest.get("mode")),
        ("运行分支", manifest.get("branch")),
        ("开始时间", manifest.get("started_at")),
        ("当前状态", lifecycle.get("status")),
        ("最终状态", lifecycle.get("final_status")),
        ("结束时间", lifecycle.get("finished_at")),
    )
    for label, value in metadata:
        safe = _display(value).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {label} | {safe} |")

    sections = (
        ("导演动作", (manifest.get("director") or {}).get("actions")),
        ("已到达检查点", (manifest.get("director") or {}).get("checkpoints")),
        ("冻结提交", manifest.get("submissions")),
        ("工作流验收视图", manifest.get("workflow_views")),
        ("故障请求与验证", manifest.get("faults")),
        ("最终告警", manifest.get("alerts")),
    )
    for title, value in sections:
        lines.extend(("", f"## {title}", "", "```json", _json_block(value), "```"))
    return "\n".join(lines) + "\n"


def render_html_report(manifest: dict[str, Any]) -> str:
    """Render a self-contained HTML report with every dynamic value escaped."""
    lifecycle = manifest.get("lifecycle") or {}
    metadata = (
        ("场景", manifest.get("scenario_name") or manifest.get("scenario_id")),
        ("场景 ID", manifest.get("scenario_id")),
        ("随机种子", manifest.get("seed")),
        ("导演模式", manifest.get("mode")),
        ("运行分支", manifest.get("branch")),
        ("开始时间", manifest.get("started_at")),
        ("当前状态", lifecycle.get("status")),
        ("最终状态", lifecycle.get("final_status")),
        ("结束时间", lifecycle.get("finished_at")),
    )
    rows = "".join(
        f"<tr><th>{escape(label)}</th><td>{escape(_display(value))}</td></tr>"
        for label, value in metadata
    )
    sections = (
        ("导演动作", (manifest.get("director") or {}).get("actions")),
        ("已到达检查点", (manifest.get("director") or {}).get("checkpoints")),
        ("冻结提交", manifest.get("submissions")),
        ("工作流验收视图", manifest.get("workflow_views")),
        ("故障请求与验证", manifest.get("faults")),
        ("最终告警", manifest.get("alerts")),
    )
    section_html = "".join(
        f"<section><h2>{escape(title)}</h2><pre>{escape(_json_block(value))}</pre></section>"
        for title, value in sections
    )
    title = f"AMOS 运行验收报告：{_display(manifest.get('run_id'))}"
    return (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        f"<title>{escape(title)}</title>"
        "<style>body{max-width:1200px;margin:32px auto;padding:0 24px;background:#f5f7fa;"
        "color:#17202a;font:14px/1.6 system-ui,sans-serif}h1,h2{color:#102a43}"
        "table{border-collapse:collapse;width:100%;background:#fff}th,td{padding:9px 12px;"
        "border:1px solid #ccd6e0;text-align:left}th{width:180px;background:#eaf0f5}"
        "section{margin-top:24px}pre{overflow:auto;padding:16px;background:#0d1b2a;color:#dbe7f3;"
        "border-radius:4px;white-space:pre-wrap}</style></head><body>"
        f"<h1>{escape(title)}</h1><h2>运行信息</h2><table>{rows}</table>{section_html}"
        "</body></html>"
    )


__all__ = [
    "RUN_MANIFEST_SCHEMA_VERSION",
    "RunManifestStore",
    "render_html_report",
    "render_markdown_report",
]
