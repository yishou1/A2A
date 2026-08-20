from __future__ import annotations

import time
import unittest
from uuid import uuid4

import redis

from commander_agent.agent_leases import AgentLeaseManager
from commander_agent.distributed_lock import RedisDistributedLock


REDIS_URL = "redis://127.0.0.1:6379/0"


class StaleDiscoveryRegistry:
    """Returns a stale idle snapshot to prove Redis, not metadata, excludes rivals."""

    def __init__(self):
        self.instance = {
            "ip": "10.0.0.1",
            "port": 8012,
            "metadata": {"role": "recon", "status": "idle"},
        }

    def discover_service(self, service_name, required_tags=None):
        if (required_tags or {}).get("role") == "recon":
            return [self.instance]
        return []

    def update_instance_metadata(self, service_name, instance, metadata_updates=None, remove_keys=None):
        instance["metadata"].update(metadata_updates or {})
        for key in remove_keys or []:
            instance["metadata"].pop(key, None)
        return instance["metadata"]


class StatusAwareRegistry(StaleDiscoveryRegistry):
    def discover_service(self, service_name, required_tags=None):
        metadata = self.instance["metadata"]
        if all(metadata.get(key) == value for key, value in (required_tags or {}).items()):
            return [self.instance]
        return []


class DistributedAgentLockTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.redis = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        try:
            cls.redis.ping()
        except redis.RedisError as exc:
            raise unittest.SkipTest(f"Redis is unavailable: {exc}")

    def setUp(self):
        self.prefix = f"a2a:test-lock:{uuid4().hex}"
        self.backends = []

    def tearDown(self):
        for backend in self.backends:
            backend.close()
        keys = list(self.redis.scan_iter(f"{self.prefix}:*"))
        if keys:
            self.redis.delete(*keys)

    def backend(self, *, ttl=2.0, renew=0.4):
        backend = RedisDistributedLock(
            REDIS_URL,
            ttl_seconds=ttl,
            renew_interval=renew,
            key_prefix=self.prefix,
        )
        self.backends.append(backend)
        return backend

    def test_two_managers_cannot_lease_the_same_stale_instance(self):
        registry = StaleDiscoveryRegistry()
        first = AgentLeaseManager(registry, distributed_lock=self.backend())
        second = AgentLeaseManager(registry, distributed_lock=self.backend())

        first_lease = first.acquire_one("recon", "wf-1", "wf-1:recon")
        second_lease = second.acquire_one("recon", "wf-2", "wf-2:recon")

        self.assertIsNotNone(first_lease)
        self.assertIsNone(second_lease)
        snapshot = first.list_leases()[0]
        self.assertTrue(snapshot["distributed_lock"])
        self.assertNotIn("token", snapshot)

        first.release(first_lease)
        replacement = second.acquire_one("recon", "wf-2", "wf-2:recon")
        self.assertIsNotNone(replacement)
        second.release(replacement)

    def test_expired_owner_cannot_delete_a_new_owners_lock(self):
        old_backend = self.backend(ttl=1.0, renew=0.2)
        new_backend = self.backend(ttl=1.0, renew=0.2)
        old_handle = old_backend.acquire("A2A-Agent:10.0.0.1:8012")
        self.assertIsNotNone(old_handle)

        old_backend.close(release=False)
        time.sleep(1.1)
        new_handle = new_backend.acquire("A2A-Agent:10.0.0.1:8012")
        self.assertIsNotNone(new_handle)

        self.assertFalse(old_backend.release(old_handle))
        self.assertTrue(new_backend.is_owned(new_handle))

    def test_renewal_keeps_long_running_lease_alive(self):
        backend = self.backend(ttl=1.0, renew=0.2)
        handle = backend.acquire("A2A-Agent:10.0.0.1:8012")
        self.assertIsNotNone(handle)

        time.sleep(1.3)

        self.assertTrue(backend.is_owned(handle))
        self.assertGreater(backend.remaining_ttl_ms(handle), 0)

    def test_expired_redis_lock_recovers_stale_nacos_busy_state(self):
        registry = StatusAwareRegistry()
        backend = self.backend(ttl=1.0, renew=0.2)
        stale_key = backend.resource_key("A2A-Agent:10.0.0.1:8012")
        registry.instance["metadata"].update(
            {
                "status": "busy",
                "lease_workflow_id": "dead-workflow",
                "lease_work_item": "dead-workflow:recon",
                "lease_lock_backend": "redis",
                "lease_lock_key": stale_key,
            }
        )
        leases = AgentLeaseManager(registry, distributed_lock=backend)

        lease = leases.acquire_one("recon", "wf-recovery", "wf-recovery:recon")

        self.assertIsNotNone(lease)
        self.assertEqual(registry.instance["metadata"]["status"], "busy")
        self.assertEqual(
            registry.instance["metadata"]["lease_workflow_id"],
            "wf-recovery",
        )
        leases.release(lease)


if __name__ == "__main__":
    unittest.main()
