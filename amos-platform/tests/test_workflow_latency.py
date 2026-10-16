"""Polling must share verified results without reapplying old assessments."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

from amos_platform.runtime.platform_runtime import PlatformRuntime


def setup_runtime(monkeypatch):
    runtime = PlatformRuntime()
    runtime._engine = SimpleNamespace(clock={"run_id": "run-current"})
    payload = {
        "workflow_id": "wf-one", "run_id": "run-current", "status": "completed",
        "package_checksum": "checksum-one", "updated_at": "version-one",
        "work_list": [], "trace": [], "result": {"status": "completed"},
    }
    runtime._bridge = SimpleNamespace(
        mode="gateway", get_workflow=lambda _id: deepcopy(payload),
        get_work_list=Mock(side_effect=AssertionError("duplicate work list")),
        get_workflow_trace=Mock(side_effect=AssertionError("duplicate trace")),
    )
    submission = {"run_id": "run-current", "transport": "gateway", "package": {"verified": True}}
    monkeypatch.setattr("amos_platform.agents.a2a.workflow_run_store.get_workflow_run_store", lambda: SimpleNamespace(get=lambda _id: submission))
    apply = Mock(return_value={"status": "completed", "applied_count": 1})
    monkeypatch.setattr("amos_platform.agents.a2a.commander_projection.apply_commander_assessments", apply)
    record = Mock()
    monkeypatch.setattr(runtime, "record_workflow_view", record)
    return runtime, payload, submission, apply, record


def test_terminal_view_is_built_and_applied_once(monkeypatch):
    runtime, payload, submission, apply, record = setup_runtime(monkeypatch)
    first = runtime.get_workflow_view("wf-one")
    first["orchestration"]["trace"].append({"bad": True})
    second = runtime.get_workflow_view("wf-one")
    assert second["orchestration"]["trace"] == []
    assert apply.call_count == 1
    assert record.call_count == 1
    # A changed result version is not hidden by the terminal cache.
    payload["updated_at"] = "version-two"
    runtime.get_workflow_view("wf-one")
    assert apply.call_count == 2
    runtime.invalidate_workflow_view("wf-one")
    runtime.get_workflow_view("wf-one")
    assert apply.call_count == 3


def test_old_projection_is_not_reapplied_after_another_stage_or_cache_eviction(monkeypatch):
    runtime, payload, submission, apply, record = setup_runtime(monkeypatch)
    runtime.get_workflow_view("wf-one")
    payload["workflow_id"] = "wf-two"
    runtime.get_workflow_view("wf-two")
    runtime._workflow_views.clear()
    payload["workflow_id"] = "wf-one"
    runtime.get_workflow_view("wf-one")
    assert apply.call_count == 2


def test_new_run_rechecks_result_integrity_and_stale_run(monkeypatch):
    runtime, payload, submission, apply, record = setup_runtime(monkeypatch)
    runtime.get_workflow_view("wf-one")
    runtime._engine.clock["run_id"] = "run-next"
    apply.return_value = {"status": "stale_run", "applied_count": 0}
    view = runtime.get_workflow_view("wf-one")
    assert view["run"]["current"] is False
    assert view["result"]["projection_status"] == "stale_run"
    assert apply.call_count == 2
    assert not runtime._workflow_views


def test_integrity_failure_is_rechecked_instead_of_cached(monkeypatch):
    runtime, payload, submission, apply, record = setup_runtime(monkeypatch)
    apply.return_value = {"status": "integrity_error", "applied_count": 0}
    runtime.get_workflow_view("wf-one")
    runtime.get_workflow_view("wf-one")
    assert apply.call_count == 2
    assert not runtime._workflow_views
