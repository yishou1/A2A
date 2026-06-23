from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote
from uuid import uuid4

import redis


RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""

RENEW_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('pexpire', KEYS[1], ARGV[2])
end
return 0
"""


@dataclass(frozen=True)
class DistributedLockHandle:
    key: str
    token: str


class RedisDistributedLock:
    """Redis-backed lock with ownership-safe release and automatic renewal."""

    def __init__(
        self,
        redis_url: str,
        *,
        ttl_seconds: float = 30.0,
        renew_interval: Optional[float] = None,
        key_prefix: str = "a2a:agent-lease",
        client=None,
    ):
        self.redis_url = redis_url
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self.ttl_ms = int(self.ttl_seconds * 1000)
        self.renew_interval = max(
            0.2,
            float(renew_interval or self.ttl_seconds / 3.0),
        )
        if self.renew_interval >= self.ttl_seconds:
            raise ValueError("Redis lock renew interval must be shorter than its TTL")
        self.key_prefix = key_prefix.rstrip(":")
        self.client = client or redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            health_check_interval=15,
        )
        self.client.ping()
        self._handles: dict[str, DistributedLockHandle] = {}
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._renew_thread = threading.Thread(
            target=self._renew_loop,
            name="a2a-redis-lock-renewal",
            daemon=True,
        )
        self._renew_thread.start()

    @classmethod
    def from_env(cls):
        enabled = os.environ.get("A2A_DISTRIBUTED_LOCK_ENABLED", "false").lower()
        if enabled not in {"1", "true", "yes", "on"}:
            return None
        redis_url = os.environ.get("A2A_REDIS_URL", "redis://127.0.0.1:6379/0")
        return cls(
            redis_url,
            ttl_seconds=float(os.environ.get("A2A_REDIS_LOCK_TTL_SECONDS", "30")),
            renew_interval=float(os.environ.get("A2A_REDIS_LOCK_RENEW_INTERVAL", "10")),
            key_prefix=os.environ.get("A2A_REDIS_LOCK_PREFIX", "a2a:agent-lease"),
        )

    def acquire(self, resource: str) -> Optional[DistributedLockHandle]:
        key = self.resource_key(resource)
        token = uuid4().hex
        acquired = self.client.set(key, token, nx=True, px=self.ttl_ms)
        if not acquired:
            return None
        handle = DistributedLockHandle(key=key, token=token)
        with self._lock:
            self._handles[key] = handle
        return handle

    def release(self, handle: DistributedLockHandle) -> bool:
        try:
            released = bool(
                self.client.eval(RELEASE_SCRIPT, 1, handle.key, handle.token)
            )
        finally:
            with self._lock:
                if self._handles.get(handle.key) == handle:
                    self._handles.pop(handle.key, None)
        return released

    def renew(self, handle: DistributedLockHandle) -> bool:
        renewed = bool(
            self.client.eval(
                RENEW_SCRIPT,
                1,
                handle.key,
                handle.token,
                self.ttl_ms,
            )
        )
        if not renewed:
            with self._lock:
                if self._handles.get(handle.key) == handle:
                    self._handles.pop(handle.key, None)
        return renewed

    def is_owned(self, handle: DistributedLockHandle) -> bool:
        try:
            return self.client.get(handle.key) == handle.token
        except redis.RedisError:
            return False

    def remaining_ttl_ms(self, handle: DistributedLockHandle) -> int:
        return int(self.client.pttl(handle.key))

    def is_key_locked(self, key: str) -> bool:
        return bool(self.client.exists(key))

    def resource_key(self, resource: str) -> str:
        return f"{self.key_prefix}:{quote(str(resource), safe='')}"

    def close(self, *, release: bool = True):
        self._stop_event.set()
        if self._renew_thread.is_alive():
            self._renew_thread.join(timeout=max(1.0, self.renew_interval + 0.5))
        if not release:
            return
        with self._lock:
            handles = list(self._handles.values())
        for handle in handles:
            try:
                self.release(handle)
            except redis.RedisError:
                pass

    def _renew_loop(self):
        while not self._stop_event.wait(self.renew_interval):
            with self._lock:
                handles = list(self._handles.values())
            for handle in handles:
                try:
                    self.renew(handle)
                except redis.RedisError:
                    # A transient Redis failure must not terminate the daemon.
                    # is_owned() remains fail-closed until Redis is reachable.
                    time.sleep(0)
