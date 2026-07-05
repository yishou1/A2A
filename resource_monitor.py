from __future__ import annotations

import os
import platform
import shutil
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

try:
    import psutil
except ImportError:  # pragma: no cover - exercised only when deps are missing.
    psutil = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _round(value: Any, digits: int = 3):
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


class ResourceMonitor:
    """Collects system and process resource metrics for an Agent runtime."""

    def __init__(
        self,
        *,
        sample_ttl_seconds: Optional[float] = None,
        cpu_warn_percent: Optional[float] = None,
        cpu_critical_percent: Optional[float] = None,
        memory_warn_percent: Optional[float] = None,
        memory_critical_percent: Optional[float] = None,
        disk_warn_percent: Optional[float] = None,
        disk_critical_percent: Optional[float] = None,
        sampler: Optional[Callable[[], dict]] = None,
    ):
        self.sample_ttl_seconds = (
            float(sample_ttl_seconds)
            if sample_ttl_seconds is not None
            else _env_float("A2A_RESOURCE_SAMPLE_TTL_SECONDS", 1.0)
        )
        self.thresholds = {
            "cpu_warn_percent": (
                float(cpu_warn_percent)
                if cpu_warn_percent is not None
                else _env_float("A2A_RESOURCE_CPU_WARN_PERCENT", 85.0)
            ),
            "cpu_critical_percent": (
                float(cpu_critical_percent)
                if cpu_critical_percent is not None
                else _env_float("A2A_RESOURCE_CPU_CRITICAL_PERCENT", 95.0)
            ),
            "memory_warn_percent": (
                float(memory_warn_percent)
                if memory_warn_percent is not None
                else _env_float("A2A_RESOURCE_MEMORY_WARN_PERCENT", 85.0)
            ),
            "memory_critical_percent": (
                float(memory_critical_percent)
                if memory_critical_percent is not None
                else _env_float("A2A_RESOURCE_MEMORY_CRITICAL_PERCENT", 95.0)
            ),
            "disk_warn_percent": (
                float(disk_warn_percent)
                if disk_warn_percent is not None
                else _env_float("A2A_RESOURCE_DISK_WARN_PERCENT", 90.0)
            ),
            "disk_critical_percent": (
                float(disk_critical_percent)
                if disk_critical_percent is not None
                else _env_float("A2A_RESOURCE_DISK_CRITICAL_PERCENT", 97.0)
            ),
        }
        self._uses_default_sampler = sampler is None
        self._sampler = sampler or self._sample_with_psutil
        self._lock = threading.RLock()
        self._last_snapshot = None
        self._last_sampled_at = 0.0
        self._process = psutil.Process(os.getpid()) if psutil is not None else None
        if self._process is not None:
            # Prime psutil's process CPU counter; the next sample is meaningful.
            try:
                self._process.cpu_percent(interval=None)
            except Exception:
                pass

    def snapshot(self, *, force: bool = False) -> dict:
        with self._lock:
            now = time.time()
            if (
                not force
                and self._last_snapshot is not None
                and now - self._last_sampled_at < self.sample_ttl_seconds
            ):
                return dict(self._last_snapshot)

            if psutil is None and self._uses_default_sampler:
                snapshot = self._unavailable_snapshot("psutil is not installed")
            else:
                try:
                    raw = self._sampler()
                    snapshot = self._build_snapshot(raw)
                except Exception as exc:
                    snapshot = self._unavailable_snapshot(str(exc))

            self._last_snapshot = snapshot
            self._last_sampled_at = now
            return dict(snapshot)

    def ready(self) -> bool:
        return self.snapshot().get("resource_state") != "critical"

    def heartbeat_metadata(self) -> dict:
        snapshot = self.snapshot()
        return {
            "resource_monitor_available": str(snapshot.get("monitor_available", False)).lower(),
            "resource_state": snapshot.get("resource_state", "unknown"),
            "resource_cpu_percent": snapshot.get("system", {}).get("cpu_percent"),
            "resource_memory_percent": snapshot.get("system", {}).get("memory_percent"),
            "resource_disk_percent": snapshot.get("system", {}).get("disk_percent"),
            "process_cpu_percent": snapshot.get("process", {}).get("cpu_percent"),
            "process_memory_mb": snapshot.get("process", {}).get("memory_rss_mb"),
            "resource_sampled_at": snapshot.get("sampled_at"),
        }

    def _sample_with_psutil(self) -> dict:
        if psutil is None:
            raise RuntimeError("psutil is not installed")

        disk_path = os.environ.get("A2A_RESOURCE_DISK_PATH") or os.getcwd()
        disk_usage = shutil.disk_usage(disk_path)
        virtual_memory = psutil.virtual_memory()
        process = self._process or psutil.Process(os.getpid())
        memory_info = process.memory_info()

        try:
            io_counters = process.io_counters()._asdict()
        except Exception:
            io_counters = {}

        try:
            open_files_count = len(process.open_files())
        except Exception:
            open_files_count = None

        return {
            "system": {
                "cpu_percent": psutil.cpu_percent(interval=None),
                "cpu_count": psutil.cpu_count(logical=True),
                "memory_total_bytes": virtual_memory.total,
                "memory_available_bytes": virtual_memory.available,
                "memory_percent": virtual_memory.percent,
                "disk_path": disk_path,
                "disk_total_bytes": disk_usage.total,
                "disk_used_bytes": disk_usage.used,
                "disk_free_bytes": disk_usage.free,
                "disk_percent": (
                    (disk_usage.used / disk_usage.total) * 100 if disk_usage.total else None
                ),
                "platform": platform.platform(),
            },
            "process": {
                "pid": os.getpid(),
                "cpu_percent": process.cpu_percent(interval=None),
                "memory_rss_bytes": memory_info.rss,
                "memory_vms_bytes": memory_info.vms,
                "num_threads": process.num_threads(),
                "open_files": open_files_count,
                "io_counters": io_counters,
            },
        }

    def _build_snapshot(self, raw: dict) -> dict:
        system = dict(raw.get("system", {}) or {})
        process = dict(raw.get("process", {}) or {})
        system["cpu_percent"] = _round(system.get("cpu_percent"))
        system["memory_percent"] = _round(system.get("memory_percent"))
        system["disk_percent"] = _round(system.get("disk_percent"))
        process["cpu_percent"] = _round(process.get("cpu_percent"))
        process["memory_rss_mb"] = _round(
            (process.get("memory_rss_bytes") or 0) / (1024 * 1024)
        )
        process["memory_vms_mb"] = _round(
            (process.get("memory_vms_bytes") or 0) / (1024 * 1024)
        )

        state, violations = self._evaluate(system)
        return {
            "monitor_available": True,
            "resource_state": state,
            "violations": violations,
            "thresholds": dict(self.thresholds),
            "sampled_at": utc_now_iso(),
            "system": system,
            "process": process,
        }

    def _evaluate(self, system: dict) -> tuple[str, list[dict]]:
        checks = [
            ("cpu_percent", system.get("cpu_percent"), "cpu"),
            ("memory_percent", system.get("memory_percent"), "memory"),
            ("disk_percent", system.get("disk_percent"), "disk"),
        ]
        state = "ok"
        violations = []
        for field, value, label in checks:
            if value is None:
                continue
            critical = self.thresholds[f"{label}_critical_percent"]
            warn = self.thresholds[f"{label}_warn_percent"]
            if value >= critical:
                state = "critical"
                violations.append(
                    {
                        "resource": label,
                        "level": "critical",
                        "value": value,
                        "threshold": critical,
                    }
                )
            elif value >= warn and state != "critical":
                state = "warn"
                violations.append(
                    {
                        "resource": label,
                        "level": "warn",
                        "value": value,
                        "threshold": warn,
                    }
                )
        return state, violations

    def _unavailable_snapshot(self, reason: str) -> dict:
        return {
            "monitor_available": False,
            "resource_state": "unknown",
            "unavailable_reason": reason,
            "thresholds": dict(self.thresholds),
            "sampled_at": utc_now_iso(),
            "system": {},
            "process": {"pid": os.getpid()},
            "violations": [],
        }
