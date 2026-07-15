from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from a2a_protocol.messages import build_task_response
from a2a_protocol.server import A2ABaseAgent
from bpel_workflow import BPELWorkflowDefinition
from commander_agent.manager_api import build_workflow_manager_app
from idempotency_store import IdempotencyStore
from protocol_contracts import (
    ContractValidationError,
    validate_task_payload,
    validate_task_response,
)
from skill_catalog import skill_contract


class ProtocolContractTest(unittest.TestCase):
    def test_task_requires_complete_envelope_and_skill_input(self):
        valid = {
            "schema_version": "1.0",
            "workflow_id": "wf-1",
            "work_item": "wf-1:1",
            "command": "scan_beach_defenses",
            "required_skill": "scan_beach_defenses",
            "input": {"sector": "Sector_A"},
            "output_hint": "recon_report",
        }
        normalized = validate_task_payload(
            valid,
            {"input_schema": skill_contract("scan_beach_defenses")["input_schema"]},
        )
        self.assertEqual(normalized, valid)

        for missing_field in valid:
            invalid = dict(valid)
            invalid.pop(missing_field)
            with self.subTest(missing_field=missing_field):
                with self.assertRaises(ContractValidationError):
                    validate_task_payload(invalid)

        invalid_input = dict(valid, input={"coordinates": "120.5E, 35.1N"})
        with self.assertRaises(ContractValidationError):
            validate_task_payload(
                invalid_input,
                {"input_schema": skill_contract("scan_beach_defenses")["input_schema"]},
            )

    def test_response_requires_output_hint_key(self):
        response = build_task_response(
            workflow_id="wf-1",
            work_item="wf-1:1",
            agent="agent-1",
            role="recon",
            output={"wrong": "value"},
        )
        with self.assertRaises(ContractValidationError):
            validate_task_response(
                {"output_hint": "recon_report"},
                response,
                {"output_schema": {"type": "string"}},
            )

    def test_bpel_rejects_expression_input_and_missing_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "invalid.bpel"
            path.write_text(
                """<process name="Invalid"><sequence><invoke
                partnerLink="ReconAgent" operation="scanBeachDefenses"
                requiredSkill="scan_beach_defenses" inputVariable="A + B"/>
                </sequence></process>""",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                BPELWorkflowDefinition.load(path)


class PersistenceTest(unittest.TestCase):
    def test_idempotency_record_survives_store_recreation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "agent.db"
            first = IdempotencyStore(path, "agent-1")
            first.put("wf-1:task-1", {"status": "completed", "output": {"x": 1}})
            second = IdempotencyStore(path, "agent-1")
            self.assertEqual(second.get("wf-1:task-1")["output"]["x"], 1)

    def test_stale_cached_response_is_reexecuted_under_current_contract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            agent = A2ABaseAgent(
                name="Contract_Test_Agent",
                description="contract test",
                role="recon",
                port=19012,
                skills=[
                    {
                        "id": "scan_beach_defenses",
                        "name": "Beach Defense Scan",
                        "description": "scan",
                        "tags": ["scan"],
                    }
                ],
                idempotency_db_path=str(Path(temp_dir) / "agent.db"),
            )
            agent.idempotency_store.put(
                "wf-cache:1",
                {"status": "completed", "output": {"legacy_result": "stale"}},
            )
            task = {
                "schema_version": "1.0",
                "workflow_id": "wf-cache",
                "work_item": "wf-cache:1",
                "command": "scan_beach_defenses",
                "required_skill": "scan_beach_defenses",
                "input": {"sector": "Sector_A"},
                "output_hint": "recon_report",
            }

            with TestClient(agent.app) as client:
                response = client.post(
                    "/sendMessage",
                    json=task,
                    headers={"Authorization": "Bearer test-token"},
                ).json()

            self.assertEqual(response["status"], "completed")
            self.assertFalse(response["cached"])
            self.assertIn("recon_report", response["output"])


class SupervisorApiTest(unittest.TestCase):
    def test_dashboard_snapshot_alerts_and_metrics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = build_workflow_manager_app(mode="local", state_dir=temp_dir)
            with TestClient(app) as client:
                self.assertEqual(client.get("/supervisor").status_code, 200)
                snapshot = client.get("/supervisor/snapshot")
                self.assertEqual(snapshot.status_code, 200)
                self.assertIn("summary", snapshot.json())
                self.assertEqual(client.get("/alerts").status_code, 200)
                metrics = client.get("/metrics")
                self.assertEqual(metrics.status_code, 200)
                self.assertIn("a2a_active_leases", metrics.text)


if __name__ == "__main__":
    unittest.main()
