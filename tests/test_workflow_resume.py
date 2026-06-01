import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from attachment_uploader import upload_attachment_file
from a2a_protocol.server import A2ABaseAgent
from commander_agent.main import CommanderAgent
from commander_agent.recovery_api import build_recovery_app
from local_runtime import LocalAgentRuntime
from workflow_payloads import build_attachment_ref, normalize_attachment_ref
from workflow_state_store import WorkflowStateStore


class WorkflowResumeTest(unittest.TestCase):
    def test_state_store_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = WorkflowStateStore(temp_dir)
            state = {
                "workflow_id": "wf-001",
                "workflow": "dynamic",
                "mode": "local",
                "status": "running",
                "context": {
                    "battle_log": ["[Recon Report] Cached"],
                    "completed_roles": ["recon"],
                    "workflow_activatity": 1,
                },
            }

            store.save("wf-001", state)
            loaded = store.load("wf-001")

            self.assertEqual(loaded["workflow_id"], "wf-001")
            self.assertEqual(loaded["context"]["battle_log"], ["[Recon Report] Cached"])
            self.assertEqual(loaded["context"]["completed_roles"], ["recon"])

    def test_commander_resume_loads_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            seed_commander = CommanderAgent(
                mode="local",
                workflow="dynamic",
                state_dir=temp_dir,
                mock_eval_score=75,
            )
            workflow_id = "wf-resume"
            context = seed_commander.initial_workflow_context()
            context["recon_report"] = "Sector_A is heavily fortified."
            context["battle_log"].append("[Recon Report] Sector_A is heavily fortified.")
            context["completed_roles"].append("recon")
            context["workflow_activatity"] = 1

            store = WorkflowStateStore(temp_dir)
            store.save(
                workflow_id,
                {
                    "workflow_id": workflow_id,
                    "workflow": "dynamic",
                    "mode": "local",
                    "status": "paused",
                    "context": context,
                },
            )

            resumed = CommanderAgent(
                mode="local",
                workflow="dynamic",
                workflow_id=workflow_id,
                state_dir=temp_dir,
                resume=True,
                mock_eval_score=75,
            )

            self.assertEqual(resumed.workflow_context["recon_report"], "Sector_A is heavily fortified.")
            payload, stream = resumed.build_task_payload("artillery", resumed.workflow_context, activatity_index=2)

            self.assertTrue(stream)
            self.assertEqual(payload["work_item"], f"{workflow_id}:2:artillery")
            self.assertEqual(payload["input"]["coordinates"], "120.5E, 35.1N")
            self.assertIn("attachments", payload)
            self.assertIn("context", payload)
            self.assertEqual(payload["context"]["completed_roles"], ["recon"])

    def test_local_runtime_replays_cached_results(self):
        runtime = LocalAgentRuntime()
        payload = {"work_item": "wf-001:1:recon", "command": "scan_beach_defenses"}

        first_response, first_events = runtime.execute("recon", payload, stream=False)
        second_response, second_events = runtime.execute("recon", payload, stream=False)

        self.assertEqual(first_response, second_response)
        self.assertEqual(first_events, second_events)

        stream_payload = {"work_item": "wf-001:2:artillery", "command": "suppress_beach_sector_A"}
        first_stream_response, first_stream_events = runtime.execute("artillery", stream_payload, stream=True)
        second_stream_response, second_stream_events = runtime.execute("artillery", stream_payload, stream=True)

        self.assertEqual(first_stream_response["mode"], "local")
        self.assertEqual(first_stream_events, second_stream_events)
        self.assertEqual(first_stream_events[-1]["status"], "Completed")

    def test_attachment_protocol_requires_object_storage_references(self):
        attachment = build_attachment_ref(
            "s3://a2a-media/beachhead/recon-01.jpg",
            sha256="abc123",
            kind="image",
            mime_type="image/jpeg",
            size_bytes=4096,
            name="recon-01.jpg",
            width=1920,
            height=1080,
        )

        self.assertEqual(attachment["uri"], "s3://a2a-media/beachhead/recon-01.jpg")
        self.assertEqual(attachment["checksum"]["algorithm"], "sha256")
        self.assertEqual(attachment["meta"]["width"], 1920)
        self.assertEqual(attachment["meta"]["height"], 1080)

        with self.assertRaises(ValueError):
            normalize_attachment_ref(
                {
                    "uri": "s3://a2a-media/beachhead/recon-raw.jpg",
                    "checksum": {"algorithm": "sha256", "value": "abc123"},
                    "data": "base64-inline-payload-is-not-allowed",
                }
            )

    def test_recovery_api_resumes_workflow_by_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workflow_id = "wf-recover"
            store = WorkflowStateStore(temp_dir)
            context = {
                "workflow_id": workflow_id,
                "workflow_mode": "local",
                "workflow_name": "dynamic",
                "workflow_status": "paused",
                "workflow_activatity": 2,
                "current_activatity": {"activatity_index": 2, "type": "agent", "role": "artillery"},
                "last_work_item": f"{workflow_id}:2:artillery",
                "last_role": "artillery",
                "sector": "Sector_A",
                "coordinates": "120.5E, 35.1N",
                "recon_report": "Sector_A is heavily fortified.",
                "strike_result": "Suppression barrage executed on Sector_A.",
                "eval_score": None,
                "commander_decision": None,
                "assault_result": None,
                "replan_result": None,
                "battle_log": [
                    "[Recon Report] Sector_A is heavily fortified.",
                    "[Artillery Report] Suppression barrage executed on Sector_A.",
                ],
                "completed_roles": ["recon", "artillery"],
                "attachments": [],
            }
            store.save(
                workflow_id,
                {
                    "workflow_id": workflow_id,
                    "workflow": "dynamic",
                    "mode": "local",
                    "status": "paused",
                    "context": context,
                },
            )

            app = build_recovery_app(default_mode="local", default_state_dir=temp_dir)
            client = TestClient(app)
            response = client.post(
                f"/workflows/{workflow_id}/resume",
                json={
                    "mode": "local",
                    "workflow": "dynamic",
                    "state_dir": temp_dir,
                    "max_steps": 1,
                    "resume": True,
                    "strict": True,
                    "mock_eval_score": 75,
                    "attachments": [
                        {
                            "uri": "s3://a2a-media/beachhead/recon-01.jpg",
                            "checksum": {"algorithm": "sha256", "value": "abc123"},
                            "kind": "image",
                            "mime_type": "image/jpeg",
                            "size_bytes": 4096,
                            "width": 1920,
                            "height": 1080,
                        }
                    ],
                },
            )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["workflow_id"], workflow_id)
            self.assertEqual(payload["context"]["workflow_activatity"], 3)
            self.assertEqual(payload["context"]["eval_score"], 75)
            self.assertEqual(payload["context"]["attachments"][0]["uri"], "s3://a2a-media/beachhead/recon-01.jpg")
            self.assertEqual(payload["context"]["attachments"][0]["checksum"]["value"], "abc123")

    def test_agent_exposes_received_work_list(self):
        agent = A2ABaseAgent(
            name="Test_Agent",
            description="Test work list visibility.",
            role="recon",
            port=9999,
        )
        client = TestClient(agent.app)
        payload = {
            "workflow_id": "wf-work-list",
            "work_item": "wf-work-list:activatity-001-recon",
            "command": "scan_beach_defenses",
            "work_list": [
                {
                    "activatity_id": "activatity-001-recon",
                    "work_item": "wf-work-list:activatity-001-recon",
                    "status": "running",
                }
            ],
        }

        response = client.post(
            "/sendMessage",
            json=payload,
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["work_item"], payload["work_item"])
        self.assertEqual(response.json()["work_list_size"], 1)

        response = client.get("/workflows/wf-work-list/work-list")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["work_list"], payload["work_list"])

    def test_legacy_checkpoint_fields_are_migrated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workflow_id = "wf-legacy"
            store = WorkflowStateStore(temp_dir)
            store.save(
                workflow_id,
                {
                    "workflow_id": workflow_id,
                    "workflow": "dynamic",
                    "mode": "local",
                    "status": "paused",
                    "context": {
                        "workflow_step": 2,
                        "current_step": {"index": 2, "type": "agent", "role": "artillery"},
                        "last_task_id": f"{workflow_id}:2:artillery",
                    },
                },
            )

            resumed = CommanderAgent(
                mode="local",
                workflow="dynamic",
                workflow_id=workflow_id,
                state_dir=temp_dir,
                resume=True,
            )
            context = resumed.workflow_context
            self.assertEqual(context["workflow_activatity"], 2)
            self.assertEqual(context["current_activatity"]["activatity_index"], 2)
            self.assertEqual(context["last_work_item"], f"{workflow_id}:2:artillery")
            self.assertNotIn("workflow_step", context)
            self.assertNotIn("last_task_id", context)

    def test_upload_attachment_file_http_put_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "recon.mp4"
            file_path.write_bytes(b"video-bytes")

            captured = {}

            class FakeResponse:
                def raise_for_status(self):
                    return None

            def fake_put(url, data=None, headers=None, timeout=None):
                captured["url"] = url
                captured["data"] = data.read() if hasattr(data, "read") else data
                captured["headers"] = headers
                captured["timeout"] = timeout
                return FakeResponse()

            with patch("attachment_uploader.requests.put", side_effect=fake_put):
                attachment = upload_attachment_file(
                    file_path,
                    "https://storage.example.com/media/recon.mp4",
                    upload_url="https://upload.example.com/presigned",
                    upload_headers={"X-Test": "1"},
                )

            self.assertEqual(captured["url"], "https://upload.example.com/presigned")
            self.assertEqual(captured["data"], b"video-bytes")
            self.assertEqual(captured["headers"]["Content-Type"], "video/mp4")
            self.assertEqual(attachment["uri"], "https://storage.example.com/media/recon.mp4")
            self.assertEqual(attachment["kind"], "video")
            self.assertEqual(attachment["size_bytes"], len(b"video-bytes"))
            self.assertEqual(attachment["checksum"]["value"], hashlib.sha256(b"video-bytes").hexdigest())

    def test_upload_attachment_file_custom_object_storage_uploader(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "recon.jpg"
            file_path.write_bytes(b"image-bytes")

            captured = {}

            def fake_uploader(**kwargs):
                captured.update(kwargs)

            attachment = upload_attachment_file(
                file_path,
                "s3://a2a-media/beachhead/recon.jpg",
                uploader=fake_uploader,
                mime_type="image/jpeg",
                meta={"width": 1920, "height": 1080},
            )

            self.assertEqual(captured["object_uri"], "s3://a2a-media/beachhead/recon.jpg")
            self.assertEqual(captured["mime_type"], "image/jpeg")
            self.assertEqual(captured["size_bytes"], len(b"image-bytes"))
            self.assertEqual(captured["checksum"]["algorithm"], "sha256")
            self.assertEqual(attachment["uri"], "s3://a2a-media/beachhead/recon.jpg")
            self.assertEqual(attachment["kind"], "image")
            self.assertEqual(attachment["meta"]["width"], 1920)
            self.assertEqual(attachment["meta"]["height"], 1080)


if __name__ == "__main__":
    unittest.main()
