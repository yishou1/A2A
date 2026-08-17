"""Shared single-process runtime for the simulation platform."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from amos_platform.config import run_database_path
from amos_platform.runtime.mode import read_platform_mode
from amos_platform.runtime.run_context import RunContext


@dataclass
class PlatformRuntime:
    """Own the process-local simulation engine and analysis adapter."""

    context: RunContext = field(default_factory=lambda: RunContext(
        platform_mode=read_platform_mode(),
    ))
    _engine: Any = None
    _bridge: Any = None
    _director: Any = None
    _run_manifest_store: Any = None

    def get_engine(self) -> Any:
        """Lazy-init and return the simulation engine."""
        if self._engine is None:
            from amos_platform.simulation.engine import SimEngine
            from amos_platform.data.scenario_repository import get_scenario

            self._engine = SimEngine()
            self._engine.lifecycle_callback = self._on_engine_lifecycle
            scenario = get_scenario(self.context.scenario_id)
            if scenario:
                self.context.seed = int(scenario.get("default_seed", self.context.seed))
                self._engine.load_scenario(scenario, seed=self.context.seed)
                self._engine.clock["scenario_id"] = self.context.scenario_id
                self._engine.clock["run_id"] = self.context.run_id
                self._engine.evaluate_media_captures()
        return self._engine

    def get_bridge(self) -> Any:
        """Lazy-init and return the configured A2A analysis adapter."""
        if self._bridge is None:
            from amos_platform.agents.commander_bridge import CommanderBridge

            self._bridge = CommanderBridge()
            self.context.agent_backend = self._bridge.mode
        return self._bridge

    def get_director(self) -> Any:
        """Lazy-init and return the server-side demonstration director."""
        if self._director is None:
            from amos_platform.agents.a2a.workflow_submission import submit_current_workflow
            from amos_platform.simulation.director import DirectorService

            def submit_checkpoint(context: dict[str, Any]) -> dict[str, Any]:
                return submit_current_workflow(
                    {
                        "scenario_id": context["scenario_id"],
                        "sim_context": True,
                        "options": {
                            "director_checkpoint_id": context.get("checkpoint_id"),
                        },
                    },
                    engine=self.get_engine(),
                    bridge=self.get_bridge(),
                    scenario_support=self.get_scenario_support(),
                    run_manifest_store=self.get_run_manifest_store(),
                )

            self._director = DirectorService(
                self,
                checkpoint_callback=submit_checkpoint,
                workflow_state_callback=self.get_workflow_view,
            )
        return self._director

    def get_workflow_view(self, workflow_id: str) -> dict[str, Any]:
        """Read one backend workflow and project verified terminal output."""
        from amos_platform.agents.a2a.commander_projection import apply_commander_assessments
        from amos_platform.agents.a2a.workflow_run_store import get_workflow_run_store
        from amos_platform.agents.a2a.workflow_view import build_workflow_view

        bridge = self.get_bridge()
        status = bridge.get_workflow(workflow_id)
        status.setdefault("workflow_id", workflow_id)
        submission = get_workflow_run_store().get(workflow_id)
        if not submission:
            run_id = str(status.get("run_id") or "")
            manifest = self.get_run_manifest_store().get(run_id) if run_id else None
            if manifest:
                archived = next((
                    row.get("snapshot")
                    for row in manifest.get("submissions") or []
                    if str(row.get("workflow_id") or "") == str(workflow_id)
                    and isinstance(row.get("snapshot"), dict)
                ), None)
                if archived:
                    submission = archived
                    get_workflow_run_store().put(workflow_id, submission)
        projection: dict[str, Any] = {}
        work_list: Any = {}
        trace: Any = {}
        if not status.get("error"):
            work_list = bridge.get_work_list(workflow_id)
            trace = bridge.get_workflow_trace(workflow_id)
            if str(status.get("status") or "").lower() == "completed":
                projection = apply_commander_assessments(
                    self.get_engine(),
                    status,
                    submission=submission,
                )
        view = build_workflow_view(
            status,
            work_list={} if isinstance(work_list, dict) and work_list.get("error") else work_list,
            trace={} if isinstance(trace, dict) and trace.get("error") else trace,
            submission=submission,
            projection=projection,
            backend_transport=bridge.mode,
            current_run_id=str(self.get_engine().clock.get("run_id") or ""),
        )
        self.record_workflow_view(view)
        return view

    def get_scenario_support(self) -> dict[str, Any]:
        """Return the small display catalog required by the active scenario."""
        from amos_platform.data.scenario_support import get_scenario_support

        return get_scenario_support()

    def get_run_manifest_store(self) -> Any:
        """Return the SQLite archive configured for the current environment."""
        from amos_platform.runtime.run_manifest import RunManifestStore

        configured_path = run_database_path().expanduser().resolve()
        if (
            self._run_manifest_store is None
            or self._run_manifest_store.db_path != configured_path
        ):
            self._run_manifest_store = RunManifestStore(configured_path)
        return self._run_manifest_store

    def begin_run(self, scenario_id: str, *, seed: int | None = None) -> RunContext:
        """Record a new active run context."""
        self.context.platform_mode = read_platform_mode()
        self.context.reset(scenario_id, seed=seed)
        from amos_platform.data.scenario_repository import get_scenario

        scenario = get_scenario(scenario_id) or {}
        self.get_run_manifest_store().begin_run(
            self.context.to_dict(),
            scenario_name=str(scenario.get("name") or "") or None,
            mode="integration",
            branch=str(scenario.get("default_branch") or "standard"),
        )
        return self.context

    def _on_engine_lifecycle(self, status: str) -> None:
        """Receive lifecycle transitions, including background completion."""
        self.record_engine_state(status=status)

    def record_engine_state(self, *, status: str | None = None) -> dict[str, Any] | None:
        """Update lifecycle and operator-safe alerts for the active engine run."""
        if self._engine is None:
            return None
        run_id = str(self._engine.clock.get("run_id") or "")
        if not run_id:
            return None
        store = self.get_run_manifest_store()
        if store.get(run_id) is None:
            return None
        return store.record_lifecycle(
            run_id,
            status or str(self._engine.clock.get("lifecycle") or "unknown"),
            alerts=self._engine.get_operator_state().get("alerts") or [],
        )

    def record_director_state(self, state: dict[str, Any]) -> dict[str, Any] | None:
        run_id = str(state.get("run_id") or "")
        if not run_id:
            return None
        return self.get_run_manifest_store().record_director_state(run_id, state)

    def record_workflow_view(self, view: dict[str, Any]) -> dict[str, Any] | None:
        run_id = str((view.get("run") or {}).get("run_id") or "")
        if not run_id:
            return None
        return self.get_run_manifest_store().record_workflow_view(run_id, view)


_PLATFORM_RUNTIME = PlatformRuntime()


def get_platform_runtime() -> PlatformRuntime:
    """Return the process-local platform runtime singleton."""
    return _PLATFORM_RUNTIME
