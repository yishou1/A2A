from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from commander_gateway.app import build_gateway_app
from commander_gateway.__main__ import configure_public_base_url
from commander_gateway.clients import CommanderClient
from commander_gateway.config import GatewayConfig
from commander_gateway.errors import GatewayError, UpstreamError
from commander_gateway.schemas import WorkflowSubmitV1
from commander_gateway.service import GatewayService
from commander_gateway.store import FileGatewayStore, canonical_json_bytes


def make_snapshot(*, run_id: str = "run-1", sequence: int = 2):
    provenance = {
        "mode": "simulation",
        "generator": "amos_simulation",
        "simulated": True,
    }
    return {
        "schema_version": "amos.simulation.snapshot.v1",
        "run_id": run_id,
        "scenario_id": "maritime-defense-interception-event",
        "sequence": sequence,
        "sim_time_ms": 2000,
        "status": "paused",
        "assets": [],
        "observations": [
            {
                "observation_id": "obs-1",
                "media_refs": [
                    {
                        "media_id": "media-radar",
                        "uri": "/static/media/radar.png",
                        "mime_type": "image/png",
                        "checksum": "radar-sha256",
                        "source_name": "AMOS-SAMPLE",
                        "provenance": provenance,
                    }
                ],
            }
        ],
        "tracks": [],
        "alerts": [],
        "network": {},
        "simulation_chains": [
            {"chain_id": "chain-1", "status": "complete"},
            {"chain_id": "chain-2", "status": "running"},
        ],
        "recent_events": [],
        "provenance": provenance,
    }


def make_events(*, run_id: str = "run-1", count: int = 3):
    return [
        {
            "schema_version": "amos.simulation.event.v1",
            "event_id": f"event-{sequence}",
            "run_id": run_id,
            "sequence": sequence,
            "sim_time_ms": sequence * 1000,
            "occurred_at": f"2026-07-13T00:00:0{sequence}Z",
            "event_type": "simulation_event",
            "phase": "detect",
            "data": {"sequence": sequence},
            "media_refs": [
                {
                    "media_id": f"media-{sequence}",
                    "uri": f"/static/media/{sequence}.png",
                    "mime_type": "image/png",
                    "checksum": f"sha256-{sequence}",
                    "source_name": "AMOS-SAMPLE",
                    "provenance": {
                        "mode": "simulation",
                        "generator": "amos_simulation",
                        "simulated": True,
                    },
                }
            ],
            "source": "amos_simulation",
            "provenance": {
                "mode": "simulation",
                "generator": "amos_simulation",
                "simulated": True,
            },
        }
        for sequence in range(1, count + 1)
    ]


def submit_payload(**overrides):
    payload = {
        "schema_version": "amos.commander.gateway.submit.v1",
        "run_id": "run-1",
        "chain_id": "chain-1",
        "workflow": "bpel",
        "workflow_file": "beachhead_workflow",
        "max_steps": 10,
        "max_workers": 3,
        "max_retries": 2,
        "retry_backoff": 0.1,
        "request_timeout": 5.0,
    }
    payload.update(overrides)
    return payload


class FakeAmosClient:
    def __init__(self, snapshot=None, events=None):
        self.snapshot = snapshot if snapshot is not None else make_snapshot()
        self.events = events if events is not None else make_events()
        self.status = {"status": "ok"}
        self.error = None
        self.event_queries = []

    def get_status(self):
        if self.error:
            raise self.error
        return copy.deepcopy(self.status)

    def get_snapshot(self):
        if self.error:
            raise self.error
        return copy.deepcopy(self.snapshot)

    def get_events(self, after_sequence=0):
        if self.error:
            raise self.error
        self.event_queries.append(after_sequence)
        return copy.deepcopy(self.events)


class FakeCommanderClient:
    def __init__(self):
        self.workflows = {}
        self.submit_calls = []
        self.resume_calls = []
        self.health_payload = {"status": "ok"}
        self.submit_error = None
        self.health_error = None
        self.work_list_error = None
        self.trace_error = None

    def health(self):
        if self.health_error:
            raise self.health_error
        return copy.deepcopy(self.health_payload)

    def submit_workflow(self, payload):
        self.submit_calls.append(copy.deepcopy(payload))
        if self.submit_error:
            raise self.submit_error
        workflow_id = payload["workflow_id"]
        state = {
            "workflow_id": workflow_id,
            "status": "queued",
            "updated_at": "2026-07-13T00:01:00Z",
        }
        self.workflows[workflow_id] = state
        return copy.deepcopy(state)

    def get_workflow(self, workflow_id):
        if workflow_id not in self.workflows:
            raise UpstreamError("COMMANDER_NOT_FOUND", "workflow not found", 404, False)
        return copy.deepcopy(self.workflows[workflow_id])

    def get_work_list(self, workflow_id):
        if self.work_list_error:
            raise self.work_list_error
        return {"workflow_id": workflow_id, "work_list": [{"id": "work-1"}]}

    def get_trace(self, workflow_id):
        if self.trace_error:
            raise self.trace_error
        return {"workflow_id": workflow_id, "trace": [{"event": "submitted"}]}

    def resume_workflow(self, workflow_id, payload):
        self.resume_calls.append((workflow_id, copy.deepcopy(payload)))
        if workflow_id not in self.workflows:
            raise UpstreamError("COMMANDER_NOT_FOUND", "workflow not found", 404, False)
        self.workflows[workflow_id]["status"] = "queued"
        return copy.deepcopy(self.workflows[workflow_id])


class GatewayTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = GatewayConfig(
            amos_base_url="http://amos.test",
            commander_base_url="http://commander.test",
            public_base_url="http://gateway.test:8030",
            state_dir=Path(self.tmp.name),
            api_token="",
            commander_token="",
            request_timeout_sec=3.0,
        )
        self.amos = FakeAmosClient()
        self.commander = FakeCommanderClient()
        self.store = FileGatewayStore(self.config.state_dir)
        self.service = GatewayService(
            self.config,
            store=self.store,
            amos_client=self.amos,
            commander_client=self.commander,
        )

    def test_configuration_defaults_environment_override_and_timeout_validation(self):
        with patch.dict(os.environ, {}, clear=True):
            config = GatewayConfig.from_env()
        self.assertEqual(config.amos_base_url, "http://127.0.0.1:5000")
        self.assertEqual(config.commander_base_url, "http://127.0.0.1:8021")
        self.assertEqual(config.public_base_url, "http://127.0.0.1:8030")
        self.assertEqual(config.state_dir, Path(".a2a_state/commander_gateway"))

        with patch.dict(
            os.environ,
            {
                "AMOS_BASE_URL": "http://amos:5000/",
                "COMMANDER_BASE_URL": "http://commander:8021/",
                "GATEWAY_PUBLIC_BASE_URL": "http://gateway:8030/",
                "GATEWAY_STATE_DIR": "/tmp/gateway-state",
                "GATEWAY_API_TOKEN": "gateway-secret",
                "COMMANDER_TOKEN": "commander-secret",
                "GATEWAY_REQUEST_TIMEOUT_SEC": "9.5",
            },
            clear=True,
        ):
            config = GatewayConfig.from_env()
        self.assertEqual(config.amos_base_url, "http://amos:5000")
        self.assertEqual(config.request_timeout_sec, 9.5)
        self.assertEqual(config.api_token, "gateway-secret")

        with patch.dict(os.environ, {"GATEWAY_REQUEST_TIMEOUT_SEC": "0"}, clear=True):
            with self.assertRaises(ValueError):
                GatewayConfig.from_env()

    def test_submit_contract_rejects_unknown_version(self):
        with self.assertRaises(ValidationError):
            WorkflowSubmitV1.model_validate(
                submit_payload(schema_version="amos.commander.gateway.submit.v2")
            )

    def test_snapshot_event_slice_is_validated_and_media_urls_are_detached(self):
        original_snapshot = copy.deepcopy(self.amos.snapshot)
        original_events = copy.deepcopy(self.amos.events)

        projection = self.service.submit(WorkflowSubmitV1.model_validate(submit_payload()))

        self.assertEqual(projection.event_cursor, 2)
        package = self.store.read_package_json(projection.package_id)
        self.assertEqual(package["schema_version"], "amos.commander.package.v1")
        self.assertEqual([item["sequence"] for item in package["events"]], [1, 2])
        self.assertEqual(
            package["snapshot"]["observations"][0]["media_refs"][0]["uri"],
            "http://amos.test/static/media/radar.png",
        )
        self.assertEqual(
            package["events"][0]["media_refs"][0]["uri"],
            "http://amos.test/static/media/1.png",
        )
        self.assertEqual(self.amos.snapshot, original_snapshot)
        self.assertEqual(self.amos.events, original_events)
        self.assertEqual(self.amos.event_queries, [0])

    def test_contract_failures_are_explicit(self):
        cases = []
        snapshot = make_snapshot(run_id="other")
        cases.append((snapshot, make_events(), "RUN_ID_MISMATCH", 409))
        snapshot = make_snapshot()
        snapshot["simulation_chains"] = []
        cases.append((snapshot, make_events(), "CHAIN_NOT_FOUND", 404))
        snapshot = make_snapshot()
        snapshot["provenance"]["simulated"] = False
        cases.append((snapshot, make_events(), "INVALID_AMOS_CONTRACT", 422))
        events = make_events()
        events[1]["sequence"] = 3
        cases.append((make_snapshot(), events, "EVENT_SEQUENCE_INVALID", 422))
        events = make_events(run_id="other")
        cases.append((make_snapshot(), events, "RUN_ID_MISMATCH", 409))
        snapshot = make_snapshot()
        snapshot.pop("network")
        cases.append((snapshot, make_events(), "INVALID_AMOS_CONTRACT", 422))
        events = make_events()
        events[0].pop("data")
        cases.append((make_snapshot(), events, "INVALID_AMOS_CONTRACT", 422))
        snapshot = make_snapshot()
        snapshot["provenance"]["generator"] = "external"
        cases.append((snapshot, make_events(), "INVALID_AMOS_CONTRACT", 422))
        events = make_events()
        events[0]["source"] = "external"
        cases.append((make_snapshot(), events, "INVALID_AMOS_CONTRACT", 422))
        events = make_events()
        events[0]["media_refs"][0].pop("checksum")
        cases.append((make_snapshot(), events, "INVALID_AMOS_CONTRACT", 422))
        snapshot = make_snapshot()
        snapshot["unexpected_top_level"] = True
        cases.append((snapshot, make_events(), "INVALID_AMOS_CONTRACT", 422))
        snapshot = make_snapshot()
        snapshot["assets"] = [{"nested": {"truth_id": "truth-1"}}]
        cases.append((snapshot, make_events(), "INVALID_AMOS_CONTRACT", 422))
        snapshot = make_snapshot()
        snapshot["recent_events"] = make_events(run_id="other", count=1)
        cases.append((snapshot, make_events(), "RUN_ID_MISMATCH", 409))
        snapshot = make_snapshot(sequence=1)
        snapshot["recent_events"] = make_events(count=2)
        cases.append((snapshot, make_events(), "EVENT_SEQUENCE_INVALID", 422))
        events = make_events()
        events[0]["data"] = {"nested": {"commander_note": "must not pass"}}
        cases.append((make_snapshot(), events, "INVALID_AMOS_CONTRACT", 422))

        for snapshot, events, code, status_code in cases:
            with self.subTest(code=code):
                service = GatewayService(
                    self.config,
                    store=FileGatewayStore(Path(self.tmp.name) / code),
                    amos_client=FakeAmosClient(snapshot, events),
                    commander_client=FakeCommanderClient(),
                )
                with self.assertRaises(GatewayError) as caught:
                    service.submit(WorkflowSubmitV1.model_validate(submit_payload()))
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(caught.exception.status_code, status_code)

    def test_canonical_package_checksum_download_and_restart(self):
        package = {"z": 1, "a": {"中文": "态势", "number": 2}}
        package_id, checksum, body = self.store.save_package(package)
        self.assertEqual(body, canonical_json_bytes(package))
        self.assertEqual(checksum, hashlib.sha256(body).hexdigest())

        restarted = FileGatewayStore(self.config.state_dir)
        read_body, read_checksum = restarted.read_package(package_id)
        self.assertEqual(read_body, body)
        self.assertEqual(read_checksum, checksum)

        app = build_gateway_app(config=self.config, service=self.service)
        client = TestClient(app)
        response = client.get(f"/gateway/v1/packages/{package_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, body)
        self.assertEqual(response.headers["x-checksum-sha256"], checksum)
        self.assertEqual(response.headers["etag"], f'"{checksum}"')

    def test_submit_is_idempotent_and_commander_attachment_is_reference_only(self):
        first = self.service.submit(WorkflowSubmitV1.model_validate(submit_payload()))
        second = self.service.submit(WorkflowSubmitV1.model_validate(submit_payload()))

        self.assertEqual(first.workflow_id, second.workflow_id)
        self.assertEqual(first.package_id, second.package_id)
        self.assertEqual(len(self.commander.submit_calls), 1)
        commander_payload = self.commander.submit_calls[0]
        self.assertEqual(first.workflow_id, "amos-" + self.store.list_idempotency()[0][:24])
        self.assertEqual(len(commander_payload["attachments"]), 1)
        attachment = commander_payload["attachments"][0]
        self.assertEqual(attachment["kind"], "amos_simulation_package")
        self.assertEqual(attachment["mime_type"], "application/json")
        self.assertEqual(
            attachment["uri"],
            f"http://gateway.test:8030/gateway/v1/packages/{first.package_id}",
        )
        self.assertEqual(attachment["checksum"]["value"], first.package_checksum)
        self.assertEqual(attachment["meta"]["run_id"], "run-1")
        self.assertEqual(attachment["meta"]["chain_id"], "chain-1")
        self.assertFalse({"data", "content", "payload"} & attachment.keys())

    def test_submission_mapping_is_durable_before_commander_request(self):
        service = self.service

        class InspectingCommander(FakeCommanderClient):
            def submit_workflow(inner_self, payload):
                record = service.store.read_workflow(payload["workflow_id"])
                self.assertEqual(record["projection"]["status"], "submitting")
                self.assertEqual(
                    record["commander_payload"]["attachments"], payload["attachments"]
                )
                return super().submit_workflow(payload)

        service.commander = InspectingCommander()
        projection = service.submit(WorkflowSubmitV1.model_validate(submit_payload()))
        self.assertEqual(projection.status, "queued")

    def test_concurrent_identical_submissions_call_commander_once(self):
        class SlowCommander(FakeCommanderClient):
            def submit_workflow(inner_self, payload):
                time.sleep(0.03)
                return super().submit_workflow(payload)

        commander = SlowCommander()
        service = GatewayService(
            self.config,
            store=self.store,
            amos_client=self.amos,
            commander_client=commander,
        )
        request = WorkflowSubmitV1.model_validate(submit_payload())
        with ThreadPoolExecutor(max_workers=6) as pool:
            projections = list(pool.map(lambda _index: service.submit(request), range(6)))
        self.assertEqual(len({item.workflow_id for item in projections}), 1)
        self.assertEqual(len(commander.submit_calls), 1)

    def test_commander_conflict_recovers_deterministic_workflow(self):
        self.commander.submit_error = UpstreamError(
            "COMMANDER_CONFLICT", "already exists", 409, False
        )

        def existing(workflow_id):
            return {"workflow_id": workflow_id, "status": "running"}

        self.commander.get_workflow = existing
        projection = self.service.submit(WorkflowSubmitV1.model_validate(submit_payload()))
        self.assertEqual(projection.status, "running")
        self.assertIsNotNone(self.store.read_workflow(projection.workflow_id))

    def test_missing_idempotency_index_is_reconciled_from_workflow_record(self):
        first = self.service.submit(WorkflowSubmitV1.model_validate(submit_payload()))
        digest = self.store.list_idempotency()[0]
        (self.store.idempotency_dir / f"{digest}.json").unlink()

        restarted_commander = FakeCommanderClient()
        restarted_commander.workflows[first.workflow_id] = {
            "workflow_id": first.workflow_id,
            "status": "queued",
        }
        restarted = GatewayService(
            self.config,
            store=FileGatewayStore(self.config.state_dir),
            amos_client=FakeAmosClient(),
            commander_client=restarted_commander,
        )
        second = restarted.submit(WorkflowSubmitV1.model_validate(submit_payload()))
        self.assertEqual(second.workflow_id, first.workflow_id)
        self.assertEqual(restarted_commander.submit_calls, [])
        self.assertEqual(restarted.store.list_idempotency(), [digest])

    def test_timeout_can_be_recovered_by_resubmitting_deterministic_id(self):
        class AmbiguousCommander(FakeCommanderClient):
            def submit_workflow(inner_self, payload):
                inner_self.submit_calls.append(copy.deepcopy(payload))
                inner_self.workflows[payload["workflow_id"]] = {
                    "workflow_id": payload["workflow_id"],
                    "status": "queued",
                }
                raise UpstreamError("COMMANDER_TIMEOUT", "timeout", 504, True)

        commander = AmbiguousCommander()
        service = GatewayService(
            self.config,
            store=self.store,
            amos_client=self.amos,
            commander_client=commander,
        )
        first = service.submit(WorkflowSubmitV1.model_validate(submit_payload()))
        second = service.submit(WorkflowSubmitV1.model_validate(submit_payload()))
        self.assertEqual(first.workflow_id, second.workflow_id)
        self.assertEqual(len(commander.submit_calls), 1)

    def test_status_empty_checkpoint_lists_and_resume_reuse_saved_payload(self):
        projection = self.service.submit(WorkflowSubmitV1.model_validate(submit_payload()))
        self.commander.work_list_error = UpstreamError(
            "COMMANDER_NOT_FOUND", "checkpoint unavailable", 404, False
        )
        self.commander.trace_error = UpstreamError(
            "COMMANDER_NOT_FOUND", "checkpoint unavailable", 404, False
        )
        current = self.service.get_projection(projection.workflow_id)
        self.assertEqual(current.work_list, [])
        self.assertEqual(current.trace, [])

        resumed = self.service.resume(projection.workflow_id)
        self.assertEqual(resumed.workflow_id, projection.workflow_id)
        workflow_id, payload = self.commander.resume_calls[0]
        self.assertEqual(workflow_id, projection.workflow_id)
        self.assertEqual(payload["attachments"], self.commander.submit_calls[0]["attachments"])
        self.assertNotIn("workflow_id", payload)

    def test_work_list_and_trace_failures_are_independent(self):
        projection = self.service.submit(WorkflowSubmitV1.model_validate(submit_payload()))
        self.commander.trace_error = UpstreamError(
            "COMMANDER_UNAVAILABLE", "trace failed", 503, True
        )
        self.assertEqual(
            self.service.get_work_list(projection.workflow_id), [{"id": "work-1"}]
        )
        self.commander.trace_error = None
        self.commander.work_list_error = UpstreamError(
            "COMMANDER_UNAVAILABLE", "work list failed", 503, True
        )
        self.assertEqual(
            self.service.get_trace(projection.workflow_id), [{"event": "submitted"}]
        )

    def test_resume_timeout_recovers_by_querying_same_workflow(self):
        projection = self.service.submit(WorkflowSubmitV1.model_validate(submit_payload()))

        def ambiguous_resume(workflow_id, payload):
            self.commander.resume_calls.append((workflow_id, copy.deepcopy(payload)))
            self.commander.workflows[workflow_id]["status"] = "running"
            raise UpstreamError("COMMANDER_TIMEOUT", "timeout", 504, True)

        self.commander.resume_workflow = ambiguous_resume
        resumed = self.service.resume(projection.workflow_id)
        self.assertEqual(resumed.workflow_id, projection.workflow_id)
        self.assertEqual(resumed.status, "running")
        persisted = self.store.read_workflow(projection.workflow_id)
        self.assertEqual(persisted["projection"]["status"], "running")

    def test_health_auth_error_shape_and_control_endpoints(self):
        secure_config = GatewayConfig(
            **{**self.config.__dict__, "api_token": "secret-token"}
        )
        app = build_gateway_app(config=secure_config, service=self.service)
        client = TestClient(app)

        health = client.get("/gateway/v1/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["status"], "ok")

        unauthorized = client.post("/gateway/v1/workflows", json=submit_payload())
        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(unauthorized.headers["www-authenticate"], "Bearer")
        self.assertEqual(unauthorized.json()["detail"]["code"], "UNAUTHORIZED")
        self.assertFalse(unauthorized.json()["detail"]["retriable"])

        submitted = client.post(
            "/gateway/v1/workflows",
            headers={"Authorization": "Bearer secret-token"},
            json=submit_payload(),
        )
        self.assertEqual(submitted.status_code, 202)
        self.assertEqual(submitted.json()["schema_version"], "amos.commander.projection.v1")
        self.assertEqual(
            set(submitted.json()),
            {
                "schema_version",
                "workflow_id",
                "run_id",
                "chain_id",
                "package_id",
                "package_checksum",
                "event_cursor",
                "status",
                "work_list",
                "trace",
                "submitted_at",
                "updated_at",
                "last_error",
            },
        )

        lowercase_scheme = client.get(
            f"/gateway/v1/workflows/{submitted.json()['workflow_id']}",
            headers={"Authorization": "bearer secret-token"},
        )
        self.assertEqual(lowercase_scheme.status_code, 200)

    def test_nondefault_cli_port_sets_matching_public_url(self):
        with patch.dict(os.environ, {}, clear=True):
            configure_public_base_url("0.0.0.0", 8040)
            self.assertEqual(
                os.environ["GATEWAY_PUBLIC_BASE_URL"], "http://127.0.0.1:8040"
            )
        with patch.dict(
            os.environ,
            {"GATEWAY_PUBLIC_BASE_URL": "https://gateway.example"},
            clear=True,
        ):
            configure_public_base_url("127.0.0.1", 8040)
            self.assertEqual(
                os.environ["GATEWAY_PUBLIC_BASE_URL"], "https://gateway.example"
            )

    def test_workflow_query_work_list_trace_and_resume_routes(self):
        app = build_gateway_app(config=self.config, service=self.service)
        client = TestClient(app)
        submitted = client.post("/gateway/v1/workflows", json=submit_payload())
        self.assertEqual(submitted.status_code, 202)
        workflow_id = submitted.json()["workflow_id"]

        current = client.get(f"/gateway/v1/workflows/{workflow_id}")
        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.json()["work_list"], [{"id": "work-1"}])
        self.assertEqual(current.json()["trace"], [{"event": "submitted"}])

        work_list = client.get(
            f"/gateway/v1/workflows/{workflow_id}/work-list"
        )
        trace = client.get(f"/gateway/v1/workflows/{workflow_id}/trace")
        self.assertEqual(work_list.json()["work_list"], [{"id": "work-1"}])
        self.assertEqual(trace.json()["trace"], [{"event": "submitted"}])

        resumed = client.post(f"/gateway/v1/workflows/{workflow_id}/resume")
        self.assertEqual(resumed.status_code, 202)
        self.assertEqual(resumed.json()["workflow_id"], workflow_id)

        missing = client.get("/gateway/v1/workflows/not-present")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["detail"]["code"], "WORKFLOW_NOT_FOUND")

    def test_health_degrades_and_upstream_errors_are_normalized(self):
        self.amos.error = UpstreamError("AMOS_UNAVAILABLE", "AMOS request failed", 503, True)
        app = build_gateway_app(config=self.config, service=self.service)
        client = TestClient(app)

        response = client.get("/gateway/v1/health")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "degraded")
        self.assertEqual(response.json()["dependencies"]["amos"]["status"], "unavailable")

        response = client.post("/gateway/v1/workflows", json=submit_payload())
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {
                "detail": {
                    "code": "AMOS_UNAVAILABLE",
                    "message": "AMOS request failed",
                    "retriable": True,
                }
            },
        )

    @patch("commander_gateway.clients.requests.request")
    def test_commander_token_is_forwarded(self, request_mock):
        response = request_mock.return_value
        response.status_code = 200
        response.json.return_value = {"status": "ok"}
        config = GatewayConfig(
            **{**self.config.__dict__, "commander_token": "commander-secret"}
        )
        CommanderClient(config).health()
        self.assertEqual(
            request_mock.call_args.kwargs["headers"]["Authorization"],
            "Bearer commander-secret",
        )

    def test_missing_and_corrupt_packages_are_reported(self):
        app = build_gateway_app(config=self.config, service=self.service)
        client = TestClient(app)
        missing = client.get("/gateway/v1/packages/missing")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["detail"]["code"], "PACKAGE_NOT_FOUND")

        package_id, _, _ = self.store.save_package({"safe": True})
        package_path = self.config.state_dir / "packages" / f"{package_id}.json"
        package_path.write_bytes(b'{"safe":false}')
        corrupt = client.get(f"/gateway/v1/packages/{package_id}")
        self.assertEqual(corrupt.status_code, 500)
        self.assertEqual(corrupt.json()["detail"]["code"], "PACKAGE_CORRUPT")

    def test_router_and_corrupt_state_errors_use_gateway_error_shape(self):
        app = build_gateway_app(config=self.config, service=self.service)
        client = TestClient(app, raise_server_exceptions=False)
        missing_route = client.get("/gateway/v1/not-a-route")
        self.assertEqual(missing_route.status_code, 404)
        self.assertEqual(missing_route.json()["detail"]["code"], "NOT_FOUND")

        projection = self.service.submit(WorkflowSubmitV1.model_validate(submit_payload()))
        self.store.save_workflow(projection.workflow_id, {"unexpected": True})
        corrupt = client.get(f"/gateway/v1/workflows/{projection.workflow_id}")
        self.assertEqual(corrupt.status_code, 500)
        self.assertEqual(corrupt.json()["detail"]["code"], "WORKFLOW_CORRUPT")


if __name__ == "__main__":
    unittest.main()
