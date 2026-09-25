"""Regression coverage for late callbacks and operator-owned pause boundaries."""
from __future__ import annotations

from copy import deepcopy
import threading
import time

import pytest

from amos_platform.runtime.platform_runtime import PlatformRuntime
from amos_platform.simulation.director import DirectorError, DirectorService


COMPLETED = {
    "status": "completed", "terminal": True, "run": {"current": True},
    "orchestration": {"counts": {"total": 1, "completed": 1, "failed": 0}},
    "result": {"projection_status": "completed"},
}


@pytest.fixture
def configured():
    runtime = PlatformRuntime()
    director = DirectorService(runtime)
    director.configure(
        scenario_id="maritime-convoy-air-defense", mode="demonstration",
        branch="standard", seed=33031,
    )
    yield runtime, director
    director._stop_auto_thread(pause=True)
    runtime.get_engine().stop()


def checkpoint(director, *, gate=False, status="running"):
    reached = {
        "checkpoint_id": "TEST-GATE" if gate else "TEST-ANALYSIS",
        "analysis_status": status,
        "analysis_blocking": False,
        "requires_operator_action": gate,
        "operator_action_type": "fire",
        "submission": {"workflow_id": "wf-test"},
    }
    director._state["current_checkpoint"] = reached
    director._reached.append(reached)
    director._scenario["engagement_policy"]["requires_prior_warning"] = False
    return reached


@pytest.mark.parametrize("already_open", [False, True])
def test_analysis_completion_does_not_resume_an_unauthorized_gate(configured, already_open):
    runtime, director = configured
    reached = checkpoint(director, gate=True)
    director._state.update(
        awaiting_authorization=already_open,
        authorization_stage="fire" if already_open else None,
        director_status="awaiting_authorization" if already_open else "checkpoint_reached",
    )
    if already_open:
        director._enter_authorization_wait()
    director.workflow_state_callback = lambda *args, **kwargs: deepcopy(COMPLETED)

    assert director._poll_current_analysis(resume_on_success=True) == "completed"
    assert reached["analysis_status"] == "completed"
    assert runtime.get_engine().clock["running"] is False
    assert director.state()["awaiting_authorization"] is already_open
    assert director.state()["director_status"] == (
        "awaiting_authorization" if already_open else "checkpoint_reached"
    )


@pytest.mark.parametrize("action", ["pause", "stop_auto"])
def test_operator_pause_during_poll_is_not_undone(configured, action):
    runtime, director = configured
    checkpoint(director)
    director._state["director_status"] = "auto_running"

    def view(*args, **kwargs):
        director.action(action)
        return deepcopy(COMPLETED)

    director.workflow_state_callback = view
    result = director._poll_current_analysis(resume_on_success=True)
    assert result == ("stale_generation" if action == "stop_auto" else "completed")
    assert director.state()["director_status"] == "paused"
    assert runtime.get_engine().clock["running"] is False


@pytest.mark.parametrize("view", [
    COMPLETED,
    {"status": "running", "terminal": False},
    {"status": "failed", "terminal": True},
])
def test_late_poll_never_changes_a_newer_checkpoint(configured, view):
    runtime, director = configured
    old = checkpoint(director)
    newer = {
        "checkpoint_id": "NEWER", "analysis_status": "running",
        "analysis_blocking": True, "submission": {"workflow_id": "wf-newer"},
    }

    def change_checkpoint(*args, **kwargs):
        director._state.update(
            current_checkpoint=newer, awaiting_analysis=True,
            director_status="awaiting_analysis", last_error="newer checkpoint detail",
        )
        director._reached.append(newer)
        return deepcopy(view)

    director.workflow_state_callback = change_checkpoint
    assert director._poll_current_analysis(resume_on_success=True) == "stale_checkpoint"
    assert director._state["current_checkpoint"] is newer
    assert newer["analysis_status"] == "running"
    assert old["analysis_status"] == "running"
    assert director.state()["awaiting_analysis"] is True
    assert director.state()["director_status"] == "awaiting_analysis"
    assert director.state()["last_error"] == "newer checkpoint detail"
    assert runtime.get_engine().clock["running"] is False


@pytest.mark.parametrize("deferred", [False, True])
def test_late_submission_never_writes_into_a_reconfigured_run(configured, monkeypatch, deferred):
    runtime, director = configured
    old_run = director.state()["run_id"]
    done = threading.Event()
    complete = director._complete_checkpoint_submission

    def finish(*args):
        try:
            return complete(*args)
        finally:
            done.set()

    monkeypatch.setattr(director, "_complete_checkpoint_submission", finish)

    def reconfigure(context):
        assert context["run_id"] == old_run
        director.configure(
            scenario_id="coastal-joint-recon-strike", mode="demonstration",
            branch="standard", seed=61023,
        )
        return {"workflow_id": "wf-old-run"}

    director.checkpoint_callback = reconfigure
    director._reach_checkpoint(director._next_checkpoint(), defer_submission=deferred)
    assert done.wait(3)
    state = director.state()
    assert state["run_id"] != old_run
    assert state["scenario_id"] == "coastal-joint-recon-strike"
    assert state["current_checkpoint"] is None
    assert state["reached_checkpoints"] == []
    assert director._checkpoint_index == -1
    assert state["director_status"] == "configured"
    assert runtime.get_engine().clock["director_status"] == "configured"


def test_stop_during_submission_preserves_checkpoint_without_resuming(configured):
    runtime, director = configured

    def stop(context):
        director.action("stop_auto")
        return {"workflow_id": "wf-stopped"}

    director.checkpoint_callback = stop
    reached = director._reach_checkpoint(director._next_checkpoint())
    assert director._state["current_checkpoint"] is reached
    assert reached["submission"]["workflow_id"] == "wf-stopped"
    assert director._checkpoint_index == 0
    assert director.state()["director_status"] == "paused"
    assert runtime.get_engine().clock["running"] is False


def test_late_submission_only_updates_its_history_entry(configured):
    runtime, director = configured
    newer = {"checkpoint_id": "NEWER", "analysis_status": "running"}

    def advance(context):
        director._state.update(current_checkpoint=newer, director_status="awaiting_analysis")
        director._reached.append(newer)
        return {"workflow_id": "wf-history"}

    director.checkpoint_callback = advance
    reached = director._reach_checkpoint(director._next_checkpoint())
    assert reached["submission"]["workflow_id"] == "wf-history"
    assert director._state["current_checkpoint"] is newer
    assert director.state()["director_status"] == "awaiting_analysis"
    assert runtime.get_engine().clock["running"] is False


def test_blocking_submission_cannot_be_skipped_by_another_control_request(configured):
    _, director = configured
    requested = dict(director._next_checkpoint(), block_until_analysis_complete=True)

    def submit(context):
        with pytest.raises(DirectorError, match="submission is not completed"):
            director.action("advance_checkpoint")
        return {"workflow_id": "wf-blocking"}

    director.checkpoint_callback = submit
    reached = director._reach_checkpoint(requested)
    assert reached["analysis_status"] == "submitted"
    assert director._checkpoint_index == 0


@pytest.mark.parametrize("status", ["submitting", "submitted", "running", "backend_unreachable"])
def test_missing_target_during_analysis_does_not_fail_the_scenario(configured, monkeypatch, status):
    runtime, director = configured
    reached = checkpoint(director, gate=True, status=status)
    # Stop the monitor after one deterministic iteration. Pretend the target
    # has already been absent longer than the debounce window.
    director._auth_target_missing_since = time.monotonic() - 5
    director._stale_sweep_at = 0
    monkeypatch.setattr(director, "_poll_stale_checkpoints", lambda run_id: director._auto_stop.set())
    director._auto_stop.clear()
    director._auto_monitor()

    assert reached["analysis_status"] == status
    assert director.state()["director_status"] != "error"
    assert director.state()["last_error"] is None
    assert director._auth_target_missing_since == 0.0
    assert runtime.get_engine().clock["running"] is False


def test_submission_failure_is_not_reported_as_completed_analysis(configured):
    runtime, director = configured
    reached = checkpoint(director, gate=True, status="submission_failed")
    reached["submission"] = {"error": "gateway timed out during submit"}
    director._auto_stop.clear()
    director._auto_monitor()
    assert director.state()["director_status"] == "error"
    assert director.state()["last_error"] == "gateway timed out during submit"
    assert runtime.get_engine().clock["running"] is False


def test_history_sweep_cannot_resume_after_stop(configured, monkeypatch):
    runtime, director = configured
    # No checkpoint is needed: previously this path could resume the engine
    # after stop while processing the no-more-checkpoints branch.
    monkeypatch.setattr(director, "_next_checkpoint", lambda: None)
    monkeypatch.setattr(director, "_poll_stale_checkpoints", lambda run_id: director.action("stop_auto"))
    director._auto_stop.clear()
    director._auto_monitor()
    assert runtime.get_engine().clock["running"] is False
    assert director.state()["director_status"] == "paused"


def test_deferred_analysis_eventually_opens_the_gate_without_resuming(configured, monkeypatch):
    runtime, director = configured
    release = threading.Event()
    checked = threading.Event()
    eligible = threading.Event()
    gate_opened = threading.Event()
    enter_wait = director._enter_authorization_wait

    def submit(context):
        assert release.wait(3)
        return {"workflow_id": "wf-slow-gate"}

    def candidate():
        checked.set()
        return eligible.is_set()

    def view(*args, **kwargs):
        eligible.set()
        return deepcopy(COMPLETED)

    def enter_gate():
        enter_wait()
        gate_opened.set()

    director.checkpoint_callback = submit
    director.workflow_state_callback = view
    director._scenario["engagement_policy"]["requires_prior_warning"] = False
    monkeypatch.setattr(director, "_authorization_candidate_exists", candidate)
    monkeypatch.setattr(director, "_enter_authorization_wait", enter_gate)
    reached = director._reach_checkpoint({
        "checkpoint_id": "SLOW-GATE", "submit_analysis": True,
        "block_until_analysis_complete": False, "requires_operator_action": True,
    }, defer_submission=True)
    director._auth_target_missing_since = time.monotonic() - 5
    director._auto_stop.clear()
    worker = threading.Thread(target=director._auto_monitor, daemon=True)
    director._auto_thread = worker
    worker.start()
    try:
        assert checked.wait(2)
        assert director.state()["director_status"] != "error"
        release.set()
        assert gate_opened.wait(2)
        state = director.state()
        assert reached["analysis_status"] == "completed"
        assert state["director_status"] == "awaiting_authorization"
        assert state["awaiting_authorization"] is True
        assert runtime.get_engine().clock["running"] is False
    finally:
        release.set()
        director._stop_auto_thread(pause=True)
        worker.join(2)
    assert not worker.is_alive()


def test_old_monitor_cannot_pause_or_publish_after_submission_resets_run(configured, monkeypatch):
    runtime, director = configured
    monkeypatch.setattr(director, "_checkpoint_satisfied", lambda cp: True)

    def reconfigure(context):
        director.configure(
            scenario_id="coastal-joint-recon-strike", mode="demonstration",
            branch="standard", seed=61023,
        )
        return {"workflow_id": "wf-old-monitor"}

    director.checkpoint_callback = reconfigure
    director._auto_stop.clear()
    director._auto_monitor()
    state = director.state()
    assert state["scenario_id"] == "coastal-joint-recon-strike"
    assert state["current_checkpoint"] is None
    assert state["director_status"] == "configured"
    assert runtime.get_engine().clock["lifecycle"] == "ready"


def test_manual_advance_cannot_step_a_run_replaced_during_poll(configured):
    runtime, director = configured
    reached = checkpoint(director)
    reached["analysis_blocking"] = True

    def reconfigure(*args, **kwargs):
        director.configure(
            scenario_id="coastal-joint-recon-strike", mode="demonstration",
            branch="standard", seed=61023,
        )
        return deepcopy(COMPLETED)

    director.workflow_state_callback = reconfigure
    with pytest.raises(DirectorError, match="运行或检查点已变化"):
        director.action("advance_checkpoint")
    assert director.state()["scenario_id"] == "coastal-joint-recon-strike"
    assert director.state()["current_checkpoint"] is None
    assert runtime.get_engine().clock["elapsed_sec"] == 0
