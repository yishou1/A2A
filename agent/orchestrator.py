"""战术情报智能体：串联三技能流水线。"""

from __future__ import annotations

import threading
from concurrent.futures import Future
from contextvars import copy_context
from typing import Any, Callable, Generic, TypeVar

from agent.models.schemas import SemanticIntelligencePacket, SensorBatch
from agent.skills.cognition.skill import CognitionSkill
from agent.skills.communication.skill import CommunicationSkill
from agent.skills.perception.skill import PerceptionSkill
from tactical_intelligence_agent.batch_preparer import finalize_batch_inference, prepare_batch_for_inference


T = TypeVar("T")


class SingleFlightCache(Generic[T]):
    """线程安全的结果缓存：同 key 计算只执行一次，其余调用等待同一个 Future。"""

    def __init__(self, max_entries: int = 1024):
        self._max_entries = max(1, int(max_entries))
        self._cache: dict[str, T] = {}
        self._inflight: dict[str, Future[T]] = {}
        self._lock = threading.RLock()

    def get_or_compute(self, key: str, compute: Callable[[], T]) -> T:
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            future = self._inflight.get(key)
            if future is None:
                future = Future()
                self._inflight[key] = future
                is_owner = True
            else:
                is_owner = False

        if not is_owner:
            return future.result()

        try:
            value = compute()
        except BaseException as exc:
            with self._lock:
                active = self._inflight.pop(key, None)
                if active is not None and not active.done():
                    active.set_exception(exc)
            raise

        with self._lock:
            self._cache[key] = value
            while len(self._cache) > self._max_entries:
                self._cache.pop(next(iter(self._cache)))
            active = self._inflight.pop(key, None)
            if active is not None and not active.done():
                active.set_result(value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._cache)


class TacticalIntelligenceAgent:
    """
    独立战术情报 Agent。

    不同 mission 可并发执行；同一 mission 使用独立锁保序，确保 MOTR/Kalman 的
    prior_tracks 与写回状态始终对应同一条时间线。
    """

    def __init__(self, *, use_mock: bool = True, config: dict[str, Any] | None = None):
        cfg = config or {}
        inference = cfg.get("inference") or {}

        def _merge(skill_cfg: dict[str, Any] | None) -> dict[str, Any]:
            return {**inference, **(skill_cfg or {})}

        self.perception = PerceptionSkill(
            use_mock=use_mock,
            config=_merge(cfg.get("perception")),
        )
        self.cognition = CognitionSkill(
            use_mock=use_mock,
            config=_merge(cfg.get("cognition")),
        )
        self.communication = CommunicationSkill(
            use_mock=use_mock,
            config=_merge(cfg.get("communication")),
        )
        self._track_state: dict[str, list[dict[str, Any]]] = {}
        self._mission_locks: dict[str, threading.Lock] = {}
        self._mission_locks_guard = threading.Lock()
        self._config = cfg

    def _mission_lock(self, mission_id: str) -> threading.Lock:
        with self._mission_locks_guard:
            lock = self._mission_locks.get(mission_id)
            if lock is None:
                lock = threading.Lock()
                self._mission_locks[mission_id] = lock
            return lock

    def process(self, batch: SensorBatch) -> SemanticIntelligencePacket:
        """处理一个批次；同 mission 串行、不同 mission 允许并发。"""
        mission_id = batch.mission_id
        with self._mission_lock(mission_id):
            return self._process_mission_batch(batch)

    def _process_mission_batch(self, batch: SensorBatch) -> SemanticIntelligencePacket:
        mission_id = batch.mission_id
        prior = list(self._track_state.get(mission_id, []))
        batch, cache_token = prepare_batch_for_inference(batch)
        try:
            perception_out = self.perception.execute(batch, prior_tracks=prior)
            self._track_state[mission_id] = perception_out.tracks

            cognition_out = self.cognition.execute(batch, perception_out)

            jamming = float(batch.context.get("jamming_level", 0.0))
            subscribers = batch.context.get("subscriber_agents") or []

            packet = self.communication.execute(
                mission_id,
                perception_out,
                cognition_out,
                subscriber_agents=subscribers,
                jamming_level=jamming,
            )

            try:
                from tactical_intelligence_agent.artifact_publisher import publish_processed_artifacts

                packet.output_attachments = publish_processed_artifacts(
                    batch,
                    perception_out,
                    config=self._config,
                )
            except Exception:
                pass

            return packet
        finally:
            finalize_batch_inference(cache_token)

    def process_inherited_context(self, batch: SensorBatch) -> SemanticIntelligencePacket:
        """供线程池调用方使用，保留调用线程的 ContextVar 上下文。"""
        return copy_context().run(self.process, batch)
