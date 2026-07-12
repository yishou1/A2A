from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from a2a_protocol.messages import build_task_response
from a2a_protocol.server import A2ABaseAgent, verify_token
from commander_agent.main import CommanderAgent, parse_args
from supervisor import SupervisorStore, build_supervisor_app
from task_pool import InMemoryTaskPoolStateStore, JsonTaskPool, TaskPoolClient, build_task_pool_app


class FrameworkClosureTest(unittest.TestCase):
    def test_task_pool_service_exposes_publish_claim_and_submit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pool = JsonTaskPool(f"{temp_dir}/task_pool.json", supervisor_required=False)
            client = TestClient(build_task_pool_app(pool))
            payload = {
                "workflow_id": "wf-service",
                "work_item": "wf-service:scan",
                "activity_skill": "scan_beach_defenses",
                "required_skills": ["scan_beach_defenses"],
            }

            published = client.post("/tasks", json={"payload": payload}).json()
            fetched = client.get("/tasks/by-work-item", params={"work_item": payload["work_item"]}).json()
            claimed = client.post(
                "/tasks/claim-next",
                json={"agent_id": "agent-1", "agent_skills": ["scan_beach_defenses"]},
            ).json()
            response = build_task_response(
                workflow_id="wf-service",
                work_item=payload["work_item"],
                agent="agent-1",
                role="scan_beach_defenses",
                output={"ok": True},
            )
            submitted = client.post(
                f"/tasks/{published['task_id']}/result",
                json={
                    "claim_id": claimed["claim_id"],
                    "agent_id": "agent-1",
                    "response": response,
                },
            ).json()

            self.assertEqual(fetched["task"]["task_id"], published["task_id"])
            self.assertTrue(claimed["claimed"])
            self.assertTrue(submitted["submitted"])
            self.assertEqual(submitted["task"]["status"], "completed")

    def test_task_pool_service_exposes_lifecycle_events(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pool = JsonTaskPool(f"{temp_dir}/task_pool.json", supervisor_required=False)
            client = TestClient(build_task_pool_app(pool))
            payload = {
                "workflow_id": "wf-events-api",
                "work_item": "wf-events-api:scan",
                "activity_skill": "scan_beach_defenses",
                "required_skills": ["scan_beach_defenses"],
            }

            published = client.post("/tasks", json={"payload": payload}).json()
            claimed = client.post(
                "/tasks/claim-next",
                json={"agent_id": "agent-1", "agent_skills": ["scan_beach_defenses"]},
            ).json()
            response = build_task_response(
                workflow_id="wf-events-api",
                work_item=payload["work_item"],
                agent="agent-1",
                role="scan_beach_defenses",
                output={"ok": True},
            )
            client.post(
                f"/tasks/{published['task_id']}/result",
                json={
                    "claim_id": claimed["claim_id"],
                    "agent_id": "agent-1",
                    "response": response,
                },
            )

            events = client.get(
                "/events",
                params={"workflow_id": "wf-events-api"},
            ).json()["events"]

            self.assertEqual(
                [event["type"] for event in events],
                ["task.created", "task.claimed", "task.completed"],
            )
            self.assertEqual(events[1]["agent_id"], "agent-1")

    def test_task_pool_client_lists_events_with_filters(self):
        client = TaskPoolClient("http://task-pool")
        calls = []

        def fake_request(method, path, payload=None, *, params=None):
            calls.append((method, path, payload, params))
            return {"events": [{"type": "task.created"}]}

        client._request = fake_request

        events = client.list_events(workflow_id="wf-1", event_type="task.created")

        self.assertEqual(events, [{"type": "task.created"}])
        self.assertEqual(calls[0][0], "GET")
        self.assertEqual(calls[0][1], "/events")
        self.assertEqual(
            calls[0][3],
            {
                "workflow_id": "wf-1",
                "task_id": None,
                "work_item": None,
                "event_type": "task.created",
            },
        )

    def test_agent_background_claim_loop_executes_available_task(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pool = JsonTaskPool(f"{temp_dir}/task_pool.json", supervisor_required=False)
            payload = {
                "workflow_id": "wf-loop",
                "work_item": "wf-loop:scan",
                "activity_skill": "scan_beach_defenses",
                "required_skills": ["scan_beach_defenses"],
                "output_hint": "scan_report",
            }
            pool.publish(payload)
            agent = A2ABaseAgent(
                name="Agent_One",
                description="test agent",
                role="recon",
                port=18001,
                supervisor_enabled=False,
                task_pool=pool,
                crowd_worker_enabled=True,
                crowd_claim_interval=0.05,
            )

            agent.start_crowd_worker()
            try:
                deadline = time.time() + 2
                task = pool.get_task_by_work_item(payload["work_item"])
                while task and task.get("status") != "completed" and time.time() < deadline:
                    time.sleep(0.05)
                    task = pool.get_task_by_work_item(payload["work_item"])
            finally:
                agent.stop_crowd_worker()

            self.assertEqual(task["status"], "completed")
            self.assertEqual(agent.metrics_snapshot()["tasks_completed"], 1)

    def test_a2a_and_supervisor_reject_invalid_bearer_when_token_configured(self):
        with patch.dict(os.environ, {"A2A_AUTH_TOKEN": "good-token", "A2A_SUPERVISOR_AUTH_TOKEN": "super-token"}):
            with self.assertRaises(HTTPException):
                verify_token("Bearer bad-token")
            self.assertEqual(verify_token("Bearer good-token"), "good-token")

            with tempfile.TemporaryDirectory() as temp_dir:
                app = build_supervisor_app(SupervisorStore(f"{temp_dir}/supervisor.json"))
                client = TestClient(app)

                denied = client.get("/agents", headers={"Authorization": "Bearer bad-token"})
                allowed = client.get("/agents", headers={"Authorization": "Bearer super-token"})
                success = client.post(
                    "/agents/agent-1/record-success",
                    headers={"Authorization": "Bearer super-token"},
                )
                failure = client.post(
                    "/agents/agent-1/record-failure",
                    json={"error_message": "boom"},
                    headers={"Authorization": "Bearer super-token"},
                )

            self.assertEqual(denied.status_code, 401)
            self.assertEqual(allowed.status_code, 200)
            self.assertEqual(success.status_code, 200)
            self.assertEqual(failure.status_code, 200)

    def test_task_pool_rejects_invalid_bearer_when_token_configured(self):
        with patch.dict(os.environ, {"A2A_TASK_POOL_AUTH_TOKEN": "pool-token"}):
            with tempfile.TemporaryDirectory() as temp_dir:
                app = build_task_pool_app(JsonTaskPool(f"{temp_dir}/task_pool.json", supervisor_required=False))
                client = TestClient(app)
                payload = {
                    "workflow_id": "wf-auth",
                    "work_item": "wf-auth:scan",
                    "activity_skill": "scan_beach_defenses",
                    "required_skills": ["scan_beach_defenses"],
                }

                denied = client.post(
                    "/tasks",
                    json={"payload": payload},
                    headers={"Authorization": "Bearer wrong-token"},
                )
                allowed = client.post(
                    "/tasks",
                    json={"payload": payload},
                    headers={"Authorization": "Bearer pool-token"},
                )

            self.assertEqual(denied.status_code, 401)
            self.assertEqual(allowed.status_code, 200)

    def test_task_pool_client_uses_auth_token_from_env(self):
        with patch.dict(os.environ, {"A2A_TASK_POOL_URL": "http://task-pool", "A2A_TASK_POOL_AUTH_TOKEN": "pool-token"}):
            client = TaskPoolClient.from_env()

        self.assertEqual(client.auth_token, "pool-token")

    def test_task_pool_can_use_injected_state_store(self):
        store = InMemoryTaskPoolStateStore()
        first_pool = JsonTaskPool(state_store=store, supervisor_required=False)
        second_pool = JsonTaskPool(state_store=store, supervisor_required=False)
        payload = {
            "workflow_id": "wf-store",
            "work_item": "wf-store:scan",
            "activity_skill": "scan_beach_defenses",
            "required_skills": ["scan_beach_defenses"],
        }

        published = first_pool.publish(payload)
        fetched = second_pool.get_task(published["task_id"])
        events = second_pool.list_events(workflow_id="wf-store")

        self.assertEqual(fetched["task_id"], published["task_id"])
        self.assertEqual([event["type"] for event in events], ["task.created"])

    def test_task_pool_cli_flags_are_parsed(self):
        argv = [
            "commander_agent/main.py",
            "--serve-task-pool",
            "--task-pool-host",
            "127.0.0.1",
            "--task-pool-port",
            "8040",
            "--task-pool-path",
            "/tmp/a2a-task-pool.json",
        ]
        with patch.object(sys, "argv", argv):
            args = parse_args()

        self.assertTrue(args.serve_task_pool)
        self.assertEqual(args.task_pool_host, "127.0.0.1")
        self.assertEqual(args.task_pool_port, 8040)

    def test_commander_uses_service_task_pool_when_url_is_configured(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"A2A_TASK_POOL_URL": "http://127.0.0.1:8040"}):
                commander = CommanderAgent(
                    mode="local",
                    workflow="dynamic",
                    workflow_id="wf-service-pool",
                    state_dir=temp_dir,
                    agent_dispatch_mode="crowd",
                )

        self.assertIsInstance(commander.task_pool, TaskPoolClient)

    def test_crowd_service_demo_builds_task_pool_cli_command(self):
        from scripts.demo_crowd_service_mode import build_agent_command, build_task_pool_command

        command = build_task_pool_command(
            host="127.0.0.1",
            port=8040,
            state_path="/tmp/a2a-task-pool.json",
        )

        self.assertIn("--serve-task-pool", command)
        self.assertIn("--task-pool-port", command)
        self.assertIn("8040", command)

        agent_command = build_agent_command(
            agent_id="crowd-recon-01",
            name="Recon_Agent",
            role="recon",
            port=18112,
            claim_interval=0.05,
        )

        self.assertIn("scripts/run_demo_agent.py", agent_command)
        self.assertIn("--role", agent_command)
        self.assertIn("recon", agent_command)


if __name__ == "__main__":
    unittest.main()
