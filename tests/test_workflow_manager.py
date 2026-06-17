from __future__ import annotations

import tempfile
import threading
import time
import unittest

from commander_agent.workflow_manager import CommanderWorkflowManager
from workflow_state_store import WorkflowStateStore


class FakeCommander:
    lock = threading.Lock()
    active = 0
    max_active = 0
    init_kwargs = {}

    @classmethod
    def reset(cls):
        with cls.lock:
            cls.active = 0
            cls.max_active = 0
            cls.init_kwargs = {}

    def __init__(self, **kwargs):
        self.workflow_id = kwargs["workflow_id"]
        self.state_store = WorkflowStateStore(kwargs["state_dir"])
        self.attachments = []
        type(self).init_kwargs[self.workflow_id] = dict(kwargs)

    def merge_external_attachments(self, attachments):
        self.attachments = attachments

    def run_bpel_workflow(self):
        with self.lock:
            type(self).active += 1
            type(self).max_active = max(type(self).max_active, type(self).active)
        try:
            time.sleep(0.15)
            context = {
                "workflow_id": self.workflow_id,
                "workflow_status": "completed",
                "attachments": self.attachments,
            }
            self.state_store.save(
                self.workflow_id,
                {
                    "workflow": "bpel",
                    "status": "completed",
                    "context": context,
                },
            )
            return context
        finally:
            with self.lock:
                type(self).active -= 1

    def run_dynamic_battle_scenario(self, max_steps=10):
        return self.run_bpel_workflow()


class CommanderWorkflowManagerTest(unittest.TestCase):
    def test_thread_pool_limits_concurrency_and_checkpoints_are_independent(self):
        FakeCommander.reset()
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = CommanderWorkflowManager(
                mode="local",
                state_dir=temp_dir,
                max_workflows=2,
                commander_factory=FakeCommander,
            )
            try:
                ids = ["wf-one", "wf-two", "wf-three"]
                for workflow_id in ids:
                    manager.submit_workflow(
                        workflow_id=workflow_id,
                        workflow_file="quick_strike_workflow",
                    )

                for workflow_id in ids:
                    result = manager.wait_for_workflow(workflow_id, timeout=2)
                    self.assertEqual(result["status"], "completed")
                    self.assertEqual(
                        result["checkpoint"]["context"]["workflow_id"],
                        workflow_id,
                    )

                self.assertEqual(FakeCommander.max_active, 2)
                self.assertEqual(len(manager.list_workflows()), 3)
                self.assertTrue(
                    all(manager.state_store.exists(workflow_id) for workflow_id in ids)
                )
            finally:
                manager.shutdown()

    def test_split_worker_limits_are_passed_to_commander(self):
        FakeCommander.reset()
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = CommanderWorkflowManager(
                mode="local",
                state_dir=temp_dir,
                max_workflows=1,
                commander_factory=FakeCommander,
            )
            try:
                manager.submit_workflow(
                    workflow_id="wf-workers",
                    workflow_file="quick_strike_workflow",
                    max_activity_workers=2,
                    max_agent_workers=7,
                )
                manager.wait_for_workflow("wf-workers", timeout=2)
                kwargs = FakeCommander.init_kwargs["wf-workers"]
                self.assertEqual(kwargs["max_activity_workers"], 2)
                self.assertEqual(kwargs["max_agent_workers"], 7)
                self.assertIsNone(kwargs["max_workers"])
            finally:
                manager.shutdown()

    def test_duplicate_active_workflow_is_rejected(self):
        FakeCommander.reset()
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = CommanderWorkflowManager(
                mode="local",
                state_dir=temp_dir,
                max_workflows=1,
                commander_factory=FakeCommander,
            )
            try:
                manager.submit_workflow(workflow_id="wf-duplicate")
                with self.assertRaisesRegex(ValueError, "already active"):
                    manager.submit_workflow(workflow_id="wf-duplicate")
            finally:
                manager.shutdown()


if __name__ == "__main__":
    unittest.main()
