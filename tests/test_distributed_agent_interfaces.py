from __future__ import annotations

import unittest

from a2a_protocol.messages import build_task_response
from a2a_protocol.server import A2ABaseAgent, default_skills_for_role
from commander_agent.agent_leases import AgentLeaseManager
from commander_agent.error_classification import classify_agent_error
from model_registry import (
    ModelRegistry,
    build_model,
    instance_has_model,
    models_from_metadata,
)
from resource_monitor import ResourceMonitor
from skill_catalog import PROFESSIONAL_SKILLS, professional_skills_for_role


def full_sampler():
    """Sampler returning all state-reporting dimensions for the interface spec."""
    return {
        "node_online": True,
        "system": {
            "cpu_percent": 12.0,
            "cpu_count": 8,
            "memory_total_bytes": 16 * 1024 * 1024 * 1024,
            "memory_available_bytes": 8 * 1024 * 1024 * 1024,
            "memory_percent": 40.0,
            "disk_path": ".",
            "disk_total_bytes": 100,
            "disk_used_bytes": 30,
            "disk_free_bytes": 70,
            "disk_percent": 30.0,
            "platform": "test",
        },
        "process": {
            "pid": 123,
            "cpu_percent": 4.5,
            "memory_rss_bytes": 64 * 1024 * 1024,
            "memory_vms_bytes": 128 * 1024 * 1024,
            "num_threads": 4,
        },
        "gpu": {
            "available": True,
            "device_count": 1,
            "gpu_percent": 55.0,
            "memory_percent": 61.0,
            "devices": [{"index": 0, "gpu_percent": 55.0, "memory_percent": 61.0}],
        },
        "energy": {"available": True, "percent": 88.0, "power_plugged": True},
        "network": {
            "available": True,
            "send_kbps": 100.0,
            "recv_kbps": 200.0,
            "bandwidth_mbps": 0.3,
            "link_stability": 0.995,
            "link_up": True,
        },
    }


class StateReportingTest(unittest.TestCase):
    """状态上报接口: CPU/GPU/内存/能源/带宽/链路稳定性/节点在线状态。"""

    def test_snapshot_reports_all_state_dimensions(self):
        monitor = ResourceMonitor(sampler=full_sampler)
        snapshot = monitor.snapshot(force=True)

        self.assertTrue(snapshot["monitor_available"])
        self.assertTrue(snapshot["node_online"])
        self.assertEqual(snapshot["system"]["cpu_percent"], 12.0)
        self.assertEqual(snapshot["system"]["memory_percent"], 40.0)
        self.assertTrue(snapshot["gpu"]["available"])
        self.assertEqual(snapshot["gpu"]["gpu_percent"], 55.0)
        self.assertTrue(snapshot["energy"]["available"])
        self.assertEqual(snapshot["energy"]["percent"], 88.0)
        self.assertTrue(snapshot["network"]["available"])
        self.assertEqual(snapshot["network"]["bandwidth_mbps"], 0.3)
        self.assertEqual(snapshot["network"]["link_stability"], 0.995)
        self.assertTrue(snapshot["network"]["link_up"])

    def test_heartbeat_metadata_flattens_new_dimensions(self):
        monitor = ResourceMonitor(sampler=full_sampler)
        metadata = monitor.heartbeat_metadata()

        self.assertEqual(metadata["node_online"], "true")
        self.assertEqual(metadata["resource_gpu_available"], "true")
        self.assertEqual(metadata["resource_gpu_percent"], 55.0)
        self.assertEqual(metadata["resource_energy_percent"], 88.0)
        self.assertEqual(metadata["resource_power_plugged"], "true")
        self.assertEqual(metadata["resource_bandwidth_mbps"], 0.3)
        self.assertEqual(metadata["resource_link_stability"], 0.995)
        self.assertEqual(metadata["resource_link_up"], "true")

    def test_missing_dimensions_degrade_gracefully(self):
        def minimal_sampler():
            return {"system": {"cpu_percent": 1.0}, "process": {"pid": 1}}

        monitor = ResourceMonitor(sampler=minimal_sampler)
        snapshot = monitor.snapshot(force=True)

        self.assertFalse(snapshot["gpu"]["available"])
        self.assertFalse(snapshot["energy"]["available"])
        self.assertFalse(snapshot["network"]["available"])
        self.assertTrue(snapshot["node_online"])


class SkillRegistrationTest(unittest.TestCase):
    """技能注册接口: 8 类专业能力。"""

    def test_catalog_covers_all_required_capabilities(self):
        required = {
            "detect",
            "locate",
            "track",
            "identify",
            "threat_evaluation",
            "target_assignment",
            "route_planning",
            "strike_effect_evaluation",
        }
        self.assertTrue(required.issubset(set(PROFESSIONAL_SKILLS.keys())))

    def test_default_role_skills_include_professional_skills(self):
        recon_skills = {skill["id"] for skill in default_skills_for_role("recon")}
        self.assertIn("scan_beach_defenses", recon_skills)  # existing demo skill preserved
        self.assertIn("detect", recon_skills)
        self.assertIn("identify", recon_skills)

    def test_professional_skills_for_role_are_isolated_copies(self):
        first = professional_skills_for_role("recon")
        first[0]["id"] = "mutated"
        second = professional_skills_for_role("recon")
        self.assertNotEqual(second[0]["id"], "mutated")


class ModelRegistryTest(unittest.TestCase):
    """算法模型: 注册/部署状态/发现。"""

    def test_metadata_and_deployment_status(self):
        registry = ModelRegistry(
            [
                build_model("m1", status="ready"),
                build_model("m2", status="loading"),
            ]
        )
        metadata = registry.metadata()
        self.assertEqual(metadata["models_count"], "2")
        self.assertIn("m1", metadata["models"])
        self.assertEqual(metadata["models_ready"], "m1")
        self.assertEqual(metadata["algorithm_deployment_status"], "partial")

    def test_all_ready_reports_ready(self):
        registry = ModelRegistry([build_model("m1"), build_model("m2")])
        self.assertEqual(registry.deployment_status(), "ready")

    def test_models_from_metadata_and_matching(self):
        metadata = {"models": "recon_detector_v1,fire_control_v1"}
        ids = models_from_metadata(metadata)
        self.assertIn("recon_detector_v1", ids)
        self.assertTrue(instance_has_model(metadata, "fire_control_v1"))
        self.assertFalse(instance_has_model(metadata, "nonexistent"))


class TaskResultTest(unittest.TestCase):
    """任务结果返回接口: 模型调用结果 + 日志标识。"""

    def test_response_carries_model_result_and_log_id(self):
        response = build_task_response(
            workflow_id="wf-1",
            work_item="wf-1:1",
            agent="A",
            role="recon",
            status="completed",
            output={"recon_report": "clear"},
            model_result={"model": "recon_detector_v1", "score": 0.92},
            log_id="trace-abc-123",
        )
        self.assertEqual(response["model_result"]["model"], "recon_detector_v1")
        self.assertEqual(response["log_id"], "trace-abc-123")

    def test_response_omits_optional_fields_when_absent(self):
        response = build_task_response(
            workflow_id="wf-1",
            work_item="wf-1:1",
            agent="A",
            role="recon",
        )
        self.assertNotIn("model_result", response)
        self.assertNotIn("log_id", response)


class ExceptionReportingTest(unittest.TestCase):
    """异常上报接口: 资源不足 + 模型调用异常。"""

    def test_resource_exhausted_classification(self):
        info = classify_agent_error("Resource exhausted: out of memory")
        self.assertEqual(info.code, "AGENT_RESOURCE_EXHAUSTED")
        self.assertEqual(info.category, "resource")
        self.assertTrue(info.failover)

    def test_resource_exhausted_chinese(self):
        info = classify_agent_error("节点资源不足，无法执行")
        self.assertEqual(info.code, "AGENT_RESOURCE_EXHAUSTED")

    def test_model_invocation_error_classification(self):
        info = classify_agent_error("Model invocation failed: inference failed")
        self.assertEqual(info.code, "MODEL_INVOCATION_ERROR")
        self.assertEqual(info.category, "model")
        self.assertFalse(info.failover)

    def test_model_invocation_error_chinese(self):
        info = classify_agent_error("模型调用超时，推理失败")
        self.assertEqual(info.code, "MODEL_INVOCATION_ERROR")


class FakeRegistry:
    def __init__(self, instances):
        self.instances = instances

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


class DelayedBindingTest(unittest.TestCase):
    """延迟绑定接口: 资源感知 + 按算法模型绑定。"""

    def _instances(self):
        return [
            {
                "ip": "10.0.0.1",
                "port": 8012,
                "metadata": {
                    "role": "recon",
                    "status": "idle",
                    "resource_cpu_percent": "95.0",
                    "models": "recon_detector_v1",
                },
            },
            {
                "ip": "10.0.0.2",
                "port": 8013,
                "metadata": {
                    "role": "recon",
                    "status": "idle",
                    "resource_cpu_percent": "10.0",
                    "models": "other_model",
                },
            },
        ]

    def test_resource_aware_prefers_less_loaded_agent(self):
        registry = FakeRegistry(self._instances())
        leases = AgentLeaseManager(registry, resource_aware=True)

        acquired = leases.acquire_one("recon", "wf-1", "wf-1:1")
        # The 10% CPU agent must be chosen over the 95% CPU agent.
        self.assertEqual(acquired.instance_key, "10.0.0.2:8013")

    def test_resource_limits_filter_overloaded_agents(self):
        registry = FakeRegistry(self._instances())
        leases = AgentLeaseManager(
            registry, resource_aware=True, resource_limits={"cpu_percent": 90.0}
        )

        first = leases.acquire_one("recon", "wf-1", "wf-1:1")
        second = leases.acquire_one("recon", "wf-2", "wf-2:1")
        self.assertEqual(first.instance_key, "10.0.0.2:8013")
        # The overloaded (95%) agent is filtered out entirely.
        self.assertIsNone(second)

    def test_default_binding_is_not_resource_aware(self):
        registry = FakeRegistry(self._instances())
        leases = AgentLeaseManager(registry)

        acquired = leases.acquire_one("recon", "wf-1", "wf-1:1")
        # Without resource awareness, discovery order (first instance) wins.
        self.assertEqual(acquired.instance_key, "10.0.0.1:8012")

    def test_required_model_filters_candidates(self):
        registry = FakeRegistry(self._instances())
        leases = AgentLeaseManager(registry)

        acquired = leases.acquire_one(
            "recon", "wf-1", "wf-1:1", required_model="recon_detector_v1"
        )
        self.assertEqual(acquired.instance_key, "10.0.0.1:8012")


class RecoveryNotificationTest(unittest.TestCase):
    """恢复通知接口 + 智能体注册接口(agent card)。"""

    def _agent(self):
        return A2ABaseAgent(
            name="Recon_Agent",
            description="test",
            role="recon",
            port=9911,
            models=[build_model("recon_detector_v1", tags=["detect"])],
        )

    def test_notify_recovery_sets_ready_and_records(self):
        agent = self._agent()
        agent.ready = False

        result = agent.notify_recovery(
            {"workflow_id": "wf-1", "action": "resume", "reason": "replanned"}
        )

        self.assertTrue(result["acknowledged"])
        self.assertTrue(agent.ready)
        notices = agent.recovery_notices()
        self.assertEqual(notices[-1]["workflow_id"], "wf-1")
        self.assertEqual(notices[-1]["action"], "resume")

    def test_agent_card_advertises_new_endpoints_and_models(self):
        agent = self._agent()
        card = agent.get_agent_card()

        self.assertEqual(card["modelsEndpoint"], "/models")
        self.assertEqual(card["recoveryEndpoint"], "/recovery/notify")
        model_ids = {model["id"] for model in card["models"]}
        self.assertIn("recon_detector_v1", model_ids)
        skill_ids = {skill["id"] for skill in card["skills"]}
        self.assertIn("detect", skill_ids)

    def test_heartbeat_metadata_includes_models_and_task_state(self):
        agent = self._agent()
        metadata = agent.heartbeat_metadata()

        self.assertIn("recon_detector_v1", metadata["models"])
        self.assertEqual(metadata["algorithm_deployment_status"], "ready")
        self.assertEqual(metadata["task_execution_status"], "idle")
        self.assertEqual(metadata["agent_run_state"], "ready")


if __name__ == "__main__":
    unittest.main()
