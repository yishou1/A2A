from __future__ import annotations

import time
import unittest

import requests

from registry.nacos_manager import AgentHeartbeatSupervisor, NacosRegistry


class FakeRegistry:
    def __init__(self):
        self.calls = []

    def send_heartbeat(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        return {"status": "ok"}


class AgentHeartbeatTest(unittest.TestCase):
    def test_heartbeat_supervisor_emits_beats(self):
        fake_registry = FakeRegistry()
        supervisor = AgentHeartbeatSupervisor(
            registry=fake_registry,
            service_name="A2A-Agent",
            ip="127.0.0.1",
            port=8002,
            metadata={"role": "recon", "status": "idle"},
            heartbeat_interval=0.05,
        )

        supervisor.start()
        deadline = time.time() + 1.0
        while time.time() < deadline and not fake_registry.calls:
            time.sleep(0.01)
        supervisor.stop()
        supervisor.join(timeout=1.0)

        self.assertTrue(fake_registry.calls)
        self.assertIn("heartbeat_ts", fake_registry.calls[0]["kwargs"]["metadata"])
        self.assertIn("heartbeat_at", fake_registry.calls[0]["kwargs"]["metadata"])

    def test_heartbeat_preserves_latest_registry_metadata(self):
        class RegistryWithLatest(FakeRegistry):
            def find_instance(self, service_name, target):
                return {
                    "ip": target["ip"],
                    "port": target["port"],
                    "metadata": {
                        "role": "recon",
                        "status": "busy",
                        "lease_workflow_id": "wf-1",
                        "lease_work_item": "wf-1:1:recon",
                        "heartbeat_ts": time.time() - 1,
                    },
                }

        fake_registry = RegistryWithLatest()
        supervisor = AgentHeartbeatSupervisor(
            registry=fake_registry,
            service_name="A2A-Agent",
            ip="127.0.0.1",
            port=8002,
            metadata={"role": "recon", "status": "idle"},
            heartbeat_interval=0.05,
        )

        supervisor.start()
        deadline = time.time() + 1.0
        while time.time() < deadline and not fake_registry.calls:
            time.sleep(0.01)
        supervisor.stop()
        supervisor.join(timeout=1.0)

        heartbeat_metadata = fake_registry.calls[0]["kwargs"]["metadata"]
        self.assertEqual(heartbeat_metadata["status"], "busy")
        self.assertEqual(heartbeat_metadata["lease_workflow_id"], "wf-1")
        self.assertEqual(heartbeat_metadata["lease_work_item"], "wf-1:1:recon")
        self.assertGreater(heartbeat_metadata["heartbeat_ts"], time.time() - 1)

    def test_heartbeat_merges_resource_metadata_provider(self):
        fake_registry = FakeRegistry()
        supervisor = AgentHeartbeatSupervisor(
            registry=fake_registry,
            service_name="A2A-Agent",
            ip="127.0.0.1",
            port=8002,
            metadata={"role": "recon", "status": "idle"},
            heartbeat_interval=0.05,
            metadata_provider=lambda: {
                "resource_cpu_percent": 88.5,
                "resource_memory_percent": 61.0,
            },
        )

        supervisor.start()
        deadline = time.time() + 1.0
        while time.time() < deadline and not fake_registry.calls:
            time.sleep(0.01)
        supervisor.stop()
        supervisor.join(timeout=1.0)

        heartbeat_metadata = fake_registry.calls[0]["kwargs"]["metadata"]
        self.assertEqual(heartbeat_metadata["status"], "idle")
        self.assertEqual(heartbeat_metadata["resource_cpu_percent"], 88.5)
        self.assertEqual(heartbeat_metadata["resource_memory_percent"], 61.0)

    def test_filter_instances_discards_stale_instances(self):
        registry = NacosRegistry(server_addresses="127.0.0.1:8848")
        registry.heartbeat_grace_seconds = 5

        now = int(time.time())
        instances = {
            "hosts": [
                {
                    "ip": "10.0.0.1",
                    "port": 8002,
                    "enabled": True,
                    "healthy": True,
                    "metadata": {"role": "recon", "status": "idle", "heartbeat_ts": now},
                },
                {
                    "ip": "10.0.0.2",
                    "port": 8003,
                    "enabled": True,
                    "healthy": True,
                    "metadata": {"role": "artillery", "status": "idle", "heartbeat_ts": now - 20},
                },
                {
                    "ip": "10.0.0.3",
                    "port": 8004,
                    "enabled": True,
                    "healthy": True,
                    "metadata": {"role": "assault", "status": "idle"},
                },
            ]
        }

        filtered = registry._filter_instances(instances)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["ip"], "10.0.0.1")

    def test_ephemeral_instance_uses_nacos_health_as_liveness_source(self):
        registry = NacosRegistry(server_addresses="127.0.0.1:8848")
        stale_timestamp = time.time() - 3600

        healthy = {
            "enabled": True,
            "healthy": True,
            "ephemeral": True,
            "metadata": {"heartbeat_ts": stale_timestamp},
        }
        unhealthy = {
            "enabled": True,
            "healthy": False,
            "ephemeral": True,
            "metadata": {"heartbeat_ts": time.time()},
        }

        self.assertTrue(registry.is_instance_fresh(healthy))
        self.assertFalse(registry.is_instance_fresh(unhealthy))

    def test_register_service_starts_heartbeat_and_adds_metadata(self):
        registry = NacosRegistry(server_addresses="127.0.0.1:8848")

        captured = {}

        def fake_add_naming_instance(service_name, ip, port, **kwargs):
            captured["service_name"] = service_name
            captured["ip"] = ip
            captured["port"] = port
            captured["kwargs"] = kwargs
            return True

        def fake_start_heartbeat(**kwargs):
            captured["heartbeat_kwargs"] = kwargs
            return object()

        registry.client.add_naming_instance = fake_add_naming_instance
        registry._start_heartbeat = fake_start_heartbeat

        registry.register_service(
            "A2A-Agent",
            "10.0.0.1",
            8002,
            metadata={"role": "recon", "status": "idle"},
            heartbeat_interval=5,
        )

        self.assertEqual(captured["kwargs"]["ephemeral"], True)
        self.assertEqual(captured["kwargs"]["heartbeat_interval"], None)
        self.assertIn("heartbeat_ts", captured["kwargs"]["metadata"])
        self.assertIn("heartbeat_at", captured["kwargs"]["metadata"])
        self.assertEqual(captured["heartbeat_kwargs"]["heartbeat_interval"], 5.0)

    def test_metadata_update_is_forwarded_to_local_heartbeat_supervisor(self):
        registry = NacosRegistry(server_addresses="127.0.0.1:8848")
        captured = {}

        class FakeSupervisor:
            def update_metadata(self, metadata):
                captured["heartbeat_metadata"] = metadata

            def stop(self):
                captured["stopped"] = True

        registry.client.modify_naming_instance = lambda *args, **kwargs: True
        registry._heartbeat_supervisors["A2A-Agent#10.0.0.1#8012"] = FakeSupervisor()
        instance = {
            "ip": "10.0.0.1",
            "port": 8012,
            "metadata": {"role": "recon", "status": "idle"},
        }

        metadata = registry.update_instance_metadata(
            "A2A-Agent",
            instance,
            metadata_updates={"status": "busy", "lease_workflow_id": "wf-1"},
        )

        self.assertEqual(metadata["status"], "busy")
        self.assertEqual(captured["heartbeat_metadata"]["lease_workflow_id"], "wf-1")

    def test_metadata_update_normalizes_invalid_cluster_name_in_http_fallback(self):
        registry = NacosRegistry(server_addresses="127.0.0.1:8848")
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                return None

        def fail_sdk(*args, **kwargs):
            raise RuntimeError("SDK unavailable")

        def fake_put(url, params, timeout):
            captured["url"] = url
            captured["params"] = params
            captured["timeout"] = timeout
            return FakeResponse()

        registry.client.modify_naming_instance = fail_sdk
        registry.http.put = fake_put
        instance = {
            "ip": "10.0.0.1",
            "port": 8012,
            "clusterName": "None",
            "metadata": {"role": "recon", "status": "idle"},
        }

        registry.update_instance_metadata(
            "A2A-Agent",
            instance,
            metadata_updates={"status": "busy"},
        )

        self.assertEqual(captured["params"]["clusterName"], "DEFAULT")
        self.assertEqual(captured["params"]["groupName"], "DEFAULT_GROUP")

    def test_metadata_update_reregisters_when_nacos_metadata_raft_is_unavailable(self):
        registry = NacosRegistry(server_addresses="127.0.0.1:8848")
        captured = {}

        class FailedResponse:
            def raise_for_status(self):
                raise requests.HTTPError("metadata Raft leader unavailable")

        class SuccessfulResponse:
            def raise_for_status(self):
                return None

        registry.http.put = lambda *args, **kwargs: FailedResponse()

        def fake_post(url, params, timeout):
            captured["url"] = url
            captured["params"] = params
            return SuccessfulResponse()

        registry.http.post = fake_post
        registry._update_instance_metadata_http(
            "A2A-Agent",
            "10.0.0.1",
            8012,
            {"role": "recon", "status": "busy"},
            cluster_name="DEFAULT",
        )

        self.assertEqual(captured["params"]["metadata"], '{"role":"recon","status":"busy"}')
        self.assertEqual(captured["params"]["clusterName"], "DEFAULT")


if __name__ == "__main__":
    unittest.main()
