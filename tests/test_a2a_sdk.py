from __future__ import annotations

import unittest

from a2a_sdk import AgentRuntimeSDK, SchedulerSDK, build_model
from a2a_protocol.server import A2ABaseAgent
from a2a_protocol.messages import build_task_response
from resource_monitor import ResourceMonitor


def full_sampler():
    return {
        "node_online": True,
        "system": {"cpu_percent": 12.0, "memory_percent": 40.0, "disk_percent": 30.0},
        "process": {"pid": 1, "memory_rss_bytes": 0},
        "gpu": {"available": True, "gpu_percent": 55.0, "memory_percent": 61.0},
        "energy": {"available": True, "percent": 88.0, "power_plugged": True},
        "network": {
            "available": True,
            "bandwidth_mbps": 0.3,
            "link_stability": 0.99,
            "link_up": True,
        },
    }


class FakeRegistry:
    """In-memory registry compatible with both SDK facades."""

    def __init__(self, instances=None):
        self.instances = instances or []
        self.registered = []

    def register_service(self, **kwargs):
        self.registered.append(kwargs)

    def discover_service(self, service_name, required_tags=None):
        return [
            instance
            for instance in self.instances
            if all(
                instance["metadata"].get(key) == value
                for key, value in (required_tags or {}).items()
            )
        ]

    def update_instance_metadata(
        self, service_name, instance, metadata_updates=None, remove_keys=None
    ):
        instance["metadata"].update(metadata_updates or {})
        for key in remove_keys or []:
            instance["metadata"].pop(key, None)
        return instance["metadata"]


class FakeClient:
    def __init__(self, target):
        self.target = target
        self.sent = None
        self.recovered = None

    def send_message(self, payload):
        self.sent = payload
        return build_task_response(
            workflow_id=payload.get("workflow_id"),
            work_item=payload.get("work_item", "w1"),
            agent="fake",
            role="recon",
            status="completed",
            output={"ok": True},
            model_result={"model": "recon_detector_v1"},
            log_id="log-1",
        )

    def notify_recovery(self, notice):
        self.recovered = notice
        return {"acknowledged": True, "target": self.target, "recovery": notice}


class AgentRuntimeSDKTest(unittest.TestCase):
    def _sdk(self, registry=None):
        return AgentRuntimeSDK(
            name="Recon_Agent",
            description="test",
            role="recon",
            port=9921,
            models=[build_model("recon_detector_v1", tags=["detect"])],
            resource_monitor=ResourceMonitor(sampler=full_sampler),
            registry=registry,
        )

    def test_facade_exposes_app_skills_and_models(self):
        sdk = self._sdk()
        self.assertIsNotNone(sdk.app)
        skill_ids = {skill["id"] for skill in sdk.skills}
        self.assertIn("detect", skill_ids)
        self.assertIn("recon_detector_v1", sdk.model_registry.model_ids())

    def test_registration_metadata_is_complete(self):
        sdk = self._sdk()
        metadata = sdk.build_registration_metadata()
        self.assertEqual(metadata["role"], "recon")
        self.assertEqual(metadata["status"], "idle")
        self.assertIn("detect", metadata["skills"])
        self.assertIn("recon_detector_v1", metadata["models"])
        self.assertEqual(metadata["resource_gpu_percent"], 55.0)
        self.assertEqual(metadata["node_online"], "true")

    def test_register_uses_provided_registry(self):
        registry = FakeRegistry()
        sdk = self._sdk(registry=registry)
        sdk.register(ip="10.0.0.9")
        self.assertEqual(len(registry.registered), 1)
        call = registry.registered[0]
        self.assertEqual(call["ip"], "10.0.0.9")
        self.assertEqual(call["port"], 9921)
        self.assertIn("recon_detector_v1", call["metadata"]["models"])
        self.assertIsNotNone(call["metadata_provider"])

    def test_from_agent_preserves_custom_agent_and_registration_metadata(self):
        registry = FakeRegistry()
        agent = A2ABaseAgent(
            name="Decision_Planning_Agent",
            description="test",
            role="decision_planning",
            port=10202,
            skills=[
                {
                    "id": "decision_planning_analysis",
                    "name": "Decision Planning Analysis",
                    "description": "test",
                }
            ],
            resource_monitor=ResourceMonitor(sampler=full_sampler),
            max_concurrent_tasks=2,
        )
        sdk = AgentRuntimeSDK.from_agent(
            agent,
            registry=registry,
            heartbeat_interval=3,
            extra_metadata={"capability": "decision_planning"},
        )

        self.assertIs(sdk.agent, agent)
        metadata = sdk.register(ip="10.0.0.9")
        self.assertEqual(metadata["skill_ids"], "decision_planning_analysis")
        self.assertEqual(metadata["max_concurrent_tasks"], "2")
        self.assertEqual(metadata["available_task_slots"], "2")
        self.assertEqual(metadata["resource_gpu_percent"], 55.0)
        self.assertEqual(metadata["capability"], "decision_planning")
        self.assertEqual(registry.registered[0]["heartbeat_interval"], 3)
        self.assertIsNotNone(registry.registered[0]["metadata_provider"])

    def test_from_agent_rejects_non_a2a_agent(self):
        with self.assertRaises(TypeError):
            AgentRuntimeSDK.from_agent(object())

    def test_register_model_at_runtime(self):
        sdk = self._sdk()
        sdk.register_model(build_model("extra_v1"))
        self.assertIn("extra_v1", sdk.model_registry.model_ids())

    def test_notify_recovery_delegates_to_agent(self):
        sdk = self._sdk()
        sdk.set_ready(False)
        result = sdk.notify_recovery({"workflow_id": "wf-1", "action": "resume"})
        self.assertTrue(result["acknowledged"])
        self.assertTrue(sdk.agent.ready)


class SchedulerSDKTest(unittest.TestCase):
    def _instances(self):
        return [
            {
                "ip": "10.0.0.1",
                "port": 8012,
                "metadata": {
                    "role": "recon",
                    "status": "idle",
                    "skills": "detect,scan_beach_defenses",
                    "models": "recon_detector_v1",
                },
            },
            {
                "ip": "10.0.0.2",
                "port": 8013,
                "metadata": {
                    "role": "recon",
                    "status": "idle",
                    "skills": "track",
                    "models": "other_model",
                },
            },
        ]

    def _sdk(self):
        registry = FakeRegistry(self._instances())
        clients = {}

        def factory(target):
            client = clients.get((target["ip"], target["port"]))
            if client is None:
                client = FakeClient(target)
                clients[(target["ip"], target["port"])] = client
            return client

        return SchedulerSDK(registry=registry, client_factory=factory), registry, clients

    def test_discover_agents_and_capabilities(self):
        sdk, _, _ = self._sdk()
        agents = sdk.discover_agents(role="recon")
        self.assertEqual(len(agents), 2)

        by_skill = sdk.discover_agents(role="recon", required_skill="detect")
        self.assertEqual(len(by_skill), 1)
        self.assertEqual(by_skill[0]["ip"], "10.0.0.1")

        by_model = sdk.discover_agents(required_model="recon_detector_v1")
        self.assertEqual(len(by_model), 1)

        models = sdk.discover_models()
        self.assertIn("recon_detector_v1", models)
        self.assertIn("other_model", models)

        skills = sdk.discover_skills()
        self.assertIn("detect", skills)

    def test_bind_dispatch_and_release(self):
        sdk, registry, clients = self._sdk()
        lease = sdk.bind_agent(
            "recon", "wf-1", "wf-1:1", required_model="recon_detector_v1"
        )
        self.assertIsNotNone(lease)
        self.assertEqual(lease.instance_key, "10.0.0.1:8012")
        self.assertEqual(lease.target["metadata"]["status"], "busy")

        response = sdk.dispatch_to_lease(lease, {"command": "scan", "workflow_id": "wf-1"})
        self.assertEqual(response["status"], "completed")
        self.assertEqual(response["model_result"]["model"], "recon_detector_v1")
        self.assertEqual(response["log_id"], "log-1")

        sdk.release(lease)
        self.assertEqual(lease.target["metadata"]["status"], "idle")

    def test_classify_error(self):
        sdk, _, _ = self._sdk()
        info = sdk.classify_error("节点资源不足")
        self.assertEqual(info.code, "AGENT_RESOURCE_EXHAUSTED")

    def test_notify_recovery_via_client(self):
        sdk, _, clients = self._sdk()
        target = {"ip": "10.0.0.1", "port": 8012}
        ack = sdk.notify_recovery(target, {"workflow_id": "wf-1", "action": "resume"})
        self.assertTrue(ack["acknowledged"])
        self.assertEqual(clients[("10.0.0.1", 8012)].recovered["workflow_id"], "wf-1")


if __name__ == "__main__":
    unittest.main()
