"""Lightweight resource snapshots for Agent discovery and diagnostics."""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from typing import Any, Dict


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentResourceMonitor:
    """Collect raw host/process metrics without making scheduling decisions."""

    def snapshot(self) -> Dict[str, Any]:
        sampled_at = _utc_now_iso()
        try:
            import psutil  # type: ignore

            process = psutil.Process(os.getpid())
            memory = psutil.virtual_memory()
            disk_path = os.getenv("A2A_RESOURCE_DISK_PATH", os.getcwd())
            disk = shutil.disk_usage(disk_path)
            process_memory = process.memory_info()
            return {
                "monitor_available": True,
                "resource_state": "observed",
                "sampled_at": sampled_at,
                "system": {
                    "cpu_percent": round(float(psutil.cpu_percent(interval=None)), 3),
                    "cpu_count": psutil.cpu_count(logical=True),
                    "memory_percent": round(float(memory.percent), 3),
                    "memory_total_bytes": int(memory.total),
                    "memory_available_bytes": int(memory.available),
                    "disk_percent": round((disk.used / disk.total) * 100, 3) if disk.total else None,
                    "disk_total_bytes": int(disk.total),
                    "disk_free_bytes": int(disk.free),
                },
                "process": {
                    "pid": os.getpid(),
                    "cpu_percent": round(float(process.cpu_percent(interval=None)), 3),
                    "memory_rss_mb": round(process_memory.rss / (1024 * 1024), 3),
                    "num_threads": process.num_threads(),
                },
                "gpu": {"available": False, "note": "CPU inference is the deployment default"},
            }
        except Exception as exc:
            return {
                "monitor_available": False,
                "resource_state": "unknown",
                "sampled_at": sampled_at,
                "system": {},
                "process": {"pid": os.getpid()},
                "gpu": {"available": False},
                "unavailable_reason": str(exc),
            }

    def heartbeat_metadata(self) -> Dict[str, str]:
        snapshot = self.snapshot()
        system = snapshot.get("system", {})
        process = snapshot.get("process", {})
        return {
            "resource_monitor_available": str(snapshot.get("monitor_available", False)).lower(),
            "resource_state": str(snapshot.get("resource_state", "unknown")),
            "resource_cpu_percent": str(system.get("cpu_percent", "")),
            "resource_memory_percent": str(system.get("memory_percent", "")),
            "resource_disk_percent": str(system.get("disk_percent", "")),
            "resource_gpu_available": "false",
            "process_cpu_percent": str(process.get("cpu_percent", "")),
            "process_memory_mb": str(process.get("memory_rss_mb", "")),
            "resource_sampled_at": str(snapshot.get("sampled_at", "")),
        }

