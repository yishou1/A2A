"""Regression tests for lookup, polling and request isolation under load."""
import asyncio
import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

from commander_gateway.app import build_gateway_app
from commander_gateway.store import FileGatewayStore
from commander_gateway.schemas import WorkflowSubmitV1
import test_commander_gateway as gateway_tests
from test_commander_gateway import submit_payload


class GatewayLatencyTest(unittest.TestCase):
    setUp = gateway_tests.GatewayTestCase.setUp
    def test_new_request_lookup_never_reads_historical_json(self):
        self.store.save_workflow("old", {"request_key": "old-key", "projection": {"large": "x" * 10000}})
        self.store.save_idempotency("old-digest", {"request_key": "old-key", "workflow_id": "old"})
        with patch.object(Path, "read_bytes", side_effect=AssertionError("archive scan")):
            self.assertIsNone(self.store.find_workflow_by_request_key("new-key"))
            self.assertIsNone(self.store.find_idempotency_by_request_key("new-key"))

    def test_legacy_archive_migrates_once_and_preserves_orphan_lookup(self):
        with tempfile.TemporaryDirectory() as directory:
            workflows = Path(directory) / "workflows"
            workflows.mkdir()
            record = {"request_key": "legacy-key"}
            (workflows / "legacy.json").write_text(json.dumps(record))
            first = FileGatewayStore(directory)
            self.assertEqual(first.find_workflow_by_request_key("legacy-key"), ("legacy", record))
            with patch.object(Path, "read_bytes", side_effect=AssertionError("startup rescan")):
                restarted = FileGatewayStore(directory)
                self.assertIsNone(restarted.find_workflow_by_request_key("missing"))

    def test_crash_between_file_and_index_commit_is_recovered(self):
        write = self.store._atomic_write

        def interrupted(path, body):
            write(path, body)
            raise OSError("simulated interruption")

        with patch.object(self.store, "_atomic_write", side_effect=interrupted):
            with self.assertRaises(OSError):
                self.store.save_workflow("orphan", {"request_key": "orphan-key"})
        restarted = FileGatewayStore(self.config.state_dir)
        self.assertEqual(restarted.find_workflow_by_request_key("orphan-key")[0], "orphan")

    def test_completed_projection_uses_one_checkpoint_and_invalidates_on_resume(self):
        workflow_id = self.service.submit(WorkflowSubmitV1.model_validate(submit_payload())).workflow_id
        state = self.commander.workflows[workflow_id]
        state.update(status="completed", finished_at="finish-one", checkpoint={"context": {
            "work_list": [{"id": "verified-work"}], "trace": [{"event": "verified-trace"}],
            "workflow_result": {"status": "completed", "summary": {"completed": 1}},
        }})
        self.commander.get_workflow_brief = lambda _id: {key: state.get(key) for key in ("status", "finished_at")}
        self.commander.get_workflow = Mock(wraps=self.commander.get_workflow)
        self.commander.get_checkpoint = Mock(side_effect=AssertionError("duplicate checkpoint"))
        self.commander.get_work_list = Mock(side_effect=AssertionError("duplicate work list"))
        self.commander.get_trace = Mock(side_effect=AssertionError("duplicate trace"))
        first = self.service.get_projection(workflow_id)
        first.trace.clear()  # Consumers cannot mutate the cached result.
        with patch.object(self.store, "save_workflow", side_effect=AssertionError("duplicate persistence")):
            second = self.service.get_projection(workflow_id)
        self.assertEqual(second.trace, [{"event": "verified-trace"}])
        self.assertEqual(self.commander.get_workflow.call_count, 1)
        self.commander.resume_workflow = lambda _id, _payload: state.update(status="running") or copy.deepcopy(state)
        resumed = self.service.resume(workflow_id)
        self.assertEqual(resumed.status, "running")
        self.assertEqual(resumed.result, {})
        state.update(status="completed", finished_at="finish-two")
        self.assertEqual(self.service.get_projection(workflow_id).status, "completed")
        self.assertEqual(self.commander.get_workflow.call_count, 3)

    def test_slow_projection_does_not_block_brief_on_same_event_loop(self):
        workflow_id = self.service.submit(WorkflowSubmitV1.model_validate(submit_payload())).workflow_id
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        original = self.commander.get_workflow

        def slow(_id):
            started.set()
            release.wait(2)
            finished.set()
            return original(_id)

        self.commander.get_workflow = slow
        self.commander.get_workflow_brief = lambda _id: {"workflow_id": _id, "status": "running"}
        app = build_gateway_app(config=self.config, service=self.service)

        async def exercise():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                full = asyncio.create_task(client.get(f"/gateway/v1/workflows/{workflow_id}"))
                try:
                    self.assertTrue(await asyncio.to_thread(started.wait, 2))
                    brief = await client.get(f"/gateway/v1/workflows/{workflow_id}/brief")
                    self.assertEqual(brief.status_code, 200)
                    self.assertFalse(finished.is_set(), "brief queued behind full projection")
                finally:
                    release.set()
                    await full

        asyncio.run(exercise())

    def test_restored_completed_cache_tracks_checkpoint_file_version(self):
        workflow_id = self.service.submit(WorkflowSubmitV1.model_validate(submit_payload())).workflow_id
        state = self.commander.workflows[workflow_id]
        state.update(status="completed", context={"workflow_result": {"status": "completed"}})
        brief = {"status": "checkpoint_only", "checkpoint_version": "file-one"}
        self.commander.get_workflow_brief = lambda _id: dict(brief)
        self.commander.get_workflow = Mock(wraps=self.commander.get_workflow)
        self.service.get_projection(workflow_id)
        self.service.get_projection(workflow_id)
        self.assertEqual(self.commander.get_workflow.call_count, 1)
        self.assertEqual(self.service.get_brief(workflow_id)["status"], "completed")
        brief["checkpoint_version"] = "file-two"
        self.assertEqual(self.service.get_brief(workflow_id)["status"], "checkpoint_only")
        self.service.get_projection(workflow_id)
        self.assertEqual(self.commander.get_workflow.call_count, 2)

    def test_missing_terminal_result_is_not_frozen_in_cache(self):
        workflow_id = self.service.submit(WorkflowSubmitV1.model_validate(submit_payload())).workflow_id
        state = self.commander.workflows[workflow_id]
        state.update(status="completed", finished_at="finish-one", checkpoint={"context": {}})
        self.commander.get_workflow_brief = lambda _id: {"status": "completed", "finished_at": "finish-one"}
        self.assertEqual(self.service.get_projection(workflow_id).result, {})
        state["checkpoint"]["context"]["workflow_result"] = {"status": "completed", "summary": {"completed": 1}}
        self.assertEqual(self.service.get_projection(workflow_id).result["summary"], {"completed": 1})
