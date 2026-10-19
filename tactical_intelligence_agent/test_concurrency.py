"""TIA 并发控制回归测试。"""

from __future__ import annotations

import threading
import time
import unittest

import numpy as np

from agent.inference.image_cache import (
    active_scope,
    begin_batch_cache,
    cache_get,
    cache_put,
    end_batch_cache,
)
from agent.models.schemas import SemanticIntelligencePacket
from agent.inference.registry import inference_guard
from agent.orchestrator import SingleFlightCache, TacticalIntelligenceAgent

try:
    from tactical_intelligence_agent.service import TacticalIntelligenceCommanderAgent
except ModuleNotFoundError as exc:  # 外部 A2A SDK 在独立仓库环境中可能不存在。
    if exc.name != "a2a_protocol":
        raise
    TacticalIntelligenceCommanderAgent = None  # type: ignore[assignment,misc]


class _CountingEngine:
    def __init__(self, delay_s: float = 0.08):
        self.delay_s = delay_s
        self.calls = 0
        self.lock = threading.Lock()

    def process(self, batch):
        with self.lock:
            self.calls += 1
        time.sleep(self.delay_s)
        return SemanticIntelligencePacket(
            mission_id=batch.mission_id,
            summary=f"processed:{batch.context.get('work_item')}",
        )


class _MissionOrderAgent(TacticalIntelligenceAgent):
    """不加载真实模型，仅验证同 mission 的状态读写顺序。"""

    def __init__(self):
        self._track_state: dict[str, list[dict]] = {}
        self._mission_locks: dict[str, threading.Lock] = {}
        self._mission_locks_guard = threading.Lock()
        self.observed_priors: list[list[dict]] = []

    def _process_mission_batch(self, batch):
        prior = list(self._track_state.get(batch.mission_id, []))
        self.observed_priors.append(prior)
        time.sleep(0.04)
        next_track = {"track_id": f"T-{len(prior) + 1}"}
        self._track_state[batch.mission_id] = [next_track]
        return SemanticIntelligencePacket(mission_id=batch.mission_id, summary=next_track["track_id"])


class ImageCacheConcurrencyTest(unittest.TestCase):
    def test_context_local_caches_do_not_interfere(self):
        first_started = threading.Event()
        second_cached = threading.Event()
        allow_first_finish = threading.Event()
        failures: list[BaseException] = []
        observed: dict[str, object] = {}

        def first_worker():
            token = begin_batch_cache("batch-a")
            try:
                cache_put("memory://a", np.array([1], dtype=np.uint8))
                first_started.set()
                if not second_cached.wait(timeout=1):
                    raise TimeoutError("second cache was not initialized")
                observed["first"] = (active_scope(), cache_get("memory://a"))
                if not allow_first_finish.wait(timeout=1):
                    raise TimeoutError("test completion signal was not sent")
            except BaseException as exc:  # pragma: no cover - test assertion below
                failures.append(exc)
            finally:
                end_batch_cache(token)

        def second_worker():
            if not first_started.wait(timeout=1):
                failures.append(TimeoutError("first cache was not initialized"))
                return
            token = begin_batch_cache("batch-b")
            try:
                cache_put("memory://b", np.array([2], dtype=np.uint8))
                second_cached.set()
                if not allow_first_finish.wait(timeout=1):
                    raise TimeoutError("test completion signal was not sent")
                observed["second"] = (active_scope(), cache_get("memory://b"))
            except BaseException as exc:  # pragma: no cover - test assertion below
                failures.append(exc)
            finally:
                end_batch_cache(token)

        first = threading.Thread(target=first_worker)
        second = threading.Thread(target=second_worker)
        first.start()
        second.start()
        self.assertTrue(second_cached.wait(timeout=1))
        allow_first_finish.set()
        first.join(timeout=1)
        second.join(timeout=1)

        self.assertFalse(failures)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        first_scope, first_value = observed["first"]
        second_scope, second_value = observed["second"]
        self.assertEqual(first_scope, "batch-a")
        self.assertEqual(second_scope, "batch-b")
        self.assertTrue(np.array_equal(first_value, np.array([1], dtype=np.uint8)))
        self.assertTrue(np.array_equal(second_value, np.array([2], dtype=np.uint8)))


class SingleFlightCacheTest(unittest.TestCase):
    def test_same_key_is_computed_once(self):
        cache: SingleFlightCache[str] = SingleFlightCache()
        calls = 0
        calls_lock = threading.Lock()
        values: list[str] = []

        def compute() -> str:
            nonlocal calls
            with calls_lock:
                calls += 1
            time.sleep(0.08)
            return "shared-result"

        threads = [
            threading.Thread(target=lambda: values.append(cache.get_or_compute("same", compute)))
            for _ in range(6)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)

        self.assertEqual(calls, 1)
        self.assertEqual(values, ["shared-result"] * 6)

    def test_different_keys_can_compute_concurrently(self):
        cache: SingleFlightCache[str] = SingleFlightCache()

        def compute(value: str) -> str:
            time.sleep(0.12)
            return value

        started_at = time.monotonic()
        threads = [
            threading.Thread(target=lambda value=value: cache.get_or_compute(value, lambda: compute(value)))
            for value in ("a", "b")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
        elapsed = time.monotonic() - started_at
        self.assertLess(elapsed, 0.22, f"different keys were serialized: {elapsed:.3f}s")


@unittest.skipIf(
    TacticalIntelligenceCommanderAgent is None,
    "a2a_protocol is not installed in this standalone environment",
)
class CommanderSingleFlightTest(unittest.TestCase):
    @staticmethod
    def _payload(work_item: str, workflow_id: str = "wf-concurrency") -> dict:
        return {
            "workflow_id": workflow_id,
            "work_item": work_item,
            "command": "process_intelligence",
            "input": {"recon_report": "concurrency test"},
        }

    def test_same_work_item_is_processed_once(self):
        engine = _CountingEngine()
        agent = TacticalIntelligenceCommanderAgent(port=0, engine=engine)
        payload = self._payload("wf-concurrency:item-1")
        packet_ids: list[str] = []
        errors: list[BaseException] = []

        def worker():
            try:
                packet_ids.append(agent._get_or_process(payload).packet_id)
            except BaseException as exc:  # pragma: no cover - test assertion below
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)

        self.assertFalse(errors)
        self.assertEqual(engine.calls, 1)
        self.assertEqual(len(set(packet_ids)), 1)

    def test_different_work_items_can_run_concurrently(self):
        engine = _CountingEngine(delay_s=0.12)
        agent = TacticalIntelligenceCommanderAgent(port=0, engine=engine)
        payloads = [self._payload(f"wf-concurrency:item-{index}") for index in range(2)]

        started_at = time.monotonic()
        threads = [threading.Thread(target=agent._get_or_process, args=(payload,)) for payload in payloads]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
        elapsed = time.monotonic() - started_at

        self.assertEqual(engine.calls, 2)
        self.assertLess(elapsed, 0.22, f"independent work items were serialized: {elapsed:.3f}s")


class InferenceGuardTest(unittest.TestCase):
    def test_same_resource_is_serialized(self):
        active = 0
        peak_active = 0
        state_lock = threading.Lock()

        def worker():
            nonlocal active, peak_active
            with inference_guard("unit-resource", {"device": "cpu", "compute_profile": "unit"}):
                with state_lock:
                    active += 1
                    peak_active = max(peak_active, active)
                time.sleep(0.04)
                with state_lock:
                    active -= 1

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
        self.assertEqual(peak_active, 1)

    def test_different_resources_can_run_concurrently(self):
        active = 0
        peak_active = 0
        state_lock = threading.Lock()

        def worker(resource: str):
            nonlocal active, peak_active
            with inference_guard(resource, {"device": "cpu", "compute_profile": "unit"}):
                with state_lock:
                    active += 1
                    peak_active = max(peak_active, active)
                time.sleep(0.06)
                with state_lock:
                    active -= 1

        threads = [threading.Thread(target=worker, args=(resource,)) for resource in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
        self.assertEqual(peak_active, 2)


class MissionOrderingTest(unittest.TestCase):
    def test_same_mission_updates_track_state_in_order(self):
        agent = _MissionOrderAgent()
        from agent.models.schemas import SensorBatch

        batches = [
            SensorBatch(mission_id="mission-1", frames=[], context={"work_item": f"item-{index}"})
            for index in range(2)
        ]
        threads = [threading.Thread(target=agent.process, args=(batch,)) for batch in batches]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)

        self.assertEqual(agent.observed_priors[0], [])
        self.assertEqual(agent.observed_priors[1], [{"track_id": "T-1"}])
        self.assertEqual(agent._track_state["mission-1"], [{"track_id": "T-2"}])


if __name__ == "__main__":
    unittest.main()
