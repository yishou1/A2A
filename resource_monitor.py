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


def _env_optional_float(name: str) -> Optional[float]:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _round(value: Any, digits: int = 3):
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


class ResourceMonitor:
    """Collects system and process resource metrics for an Agent runtime.

    The monitor only samples raw values (CPU, GPU, memory, energy, network
    bandwidth, link stability, node online state) and reports them; it does not
    classify health or make scheduling decisions.
    """

    def __init__(
        self,
        *,
        sample_ttl_seconds: Optional[float] = None,
        sampler: Optional[Callable[[], dict]] = None,
    ):
        self.sample_ttl_seconds = (
            float(sample_ttl_seconds)
            if sample_ttl_seconds is not None
            else _env_float("A2A_RESOURCE_SAMPLE_TTL_SECONDS", 1.0)
        )
        self._uses_default_sampler = sampler is None
        self._sampler = sampler or self._sample_with_psutil
        self._lock = threading.RLock()
        self._last_snapshot = None
        self._last_sampled_at = 0.0
        # Previous network counters, used to derive bandwidth rates between samples.
        self._last_net_sample = None  # (timestamp, bytes_sent, bytes_recv)
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

    def heartbeat_metadata(self) -> dict:
        snapshot = self.snapshot()
        system = snapshot.get("system", {}) or {}
        process = snapshot.get("process", {}) or {}
        gpu = snapshot.get("gpu", {}) or {}
        energy = snapshot.get("energy", {}) or {}
        network = snapshot.get("network", {}) or {}
        return {
            "resource_monitor_available": str(snapshot.get("monitor_available", False)).lower(),
            "node_online": str(snapshot.get("node_online", False)).lower(),
            "resource_cpu_percent": system.get("cpu_percent"),
            "resource_memory_percent": system.get("memory_percent"),
            "resource_disk_percent": system.get("disk_percent"),
            "process_cpu_percent": process.get("cpu_percent"),
            "process_memory_mb": process.get("memory_rss_mb"),
            "resource_gpu_available": str(gpu.get("available", False)).lower(),
            "resource_gpu_percent": gpu.get("gpu_percent"),
            "resource_gpu_memory_percent": gpu.get("memory_percent"),
            "resource_energy_available": str(energy.get("available", False)).lower(),
            "resource_energy_percent": energy.get("percent"),
            "resource_power_plugged": (
                None
                if energy.get("power_plugged") is None
                else str(energy.get("power_plugged")).lower()
            ),
            "resource_net_sent_kbps": network.get("send_kbps"),
            "resource_net_recv_kbps": network.get("recv_kbps"),
            "resource_bandwidth_mbps": network.get("bandwidth_mbps"),
            "resource_link_stability": network.get("link_stability"),
            "resource_link_up": (
                None
                if network.get("link_up") is None
                else str(network.get("link_up")).lower()
            ),
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
            "node_online": True,
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
            "gpu": self._sample_gpu(),
            "energy": self._sample_energy(),
            "network": self._sample_network(),
        }

    def _sample_gpu(self) -> dict:
        """Sample GPU utilization. Falls back gracefully when no GPU/driver."""
        override_percent = _env_optional_float("A2A_RESOURCE_GPU_PERCENT")
        override_memory = _env_optional_float("A2A_RESOURCE_GPU_MEMORY_PERCENT")
        if override_percent is not None or override_memory is not None:
            return {
                "available": True,
                "source": "env-override",
                "device_count": 1,
                "gpu_percent": override_percent,
                "memory_percent": override_memory,
                "devices": [
                    {
                        "index": 0,
                        "gpu_percent": override_percent,
                        "memory_percent": override_memory,
                    }
                ],
            }

        try:
            import pynvml  # type: ignore
        except Exception:
            return {"available": False, "reason": "pynvml not installed", "devices": []}

        try:
            pynvml.nvmlInit()
            count = pynvml.nvmlDeviceGetCount()
            devices = []
            gpu_percents = []
            memory_percents = []
            for idx in range(count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(idx)
                util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                name = pynvml.nvmlDeviceGetName(handle)
                if isinstance(name, bytes):
                    name = name.decode("utf-8", "replace")
                mem_percent = (mem.used / mem.total) * 100 if mem.total else None
                gpu_percents.append(float(util.gpu))
                if mem_percent is not None:
                    memory_percents.append(mem_percent)
                devices.append(
                    {
                        "index": idx,
                        "name": name,
                        "gpu_percent": float(util.gpu),
                        "memory_percent": mem_percent,
                        "memory_used_bytes": int(mem.used),
                        "memory_total_bytes": int(mem.total),
                    }
                )
            pynvml.nvmlShutdown()
            return {
                "available": True,
                "source": "pynvml",
                "device_count": count,
                "gpu_percent": max(gpu_percents) if gpu_percents else None,
                "memory_percent": max(memory_percents) if memory_percents else None,
                "devices": devices,
            }
        except Exception as exc:
            return {"available": False, "reason": str(exc), "devices": []}

    def _sample_energy(self) -> dict:
        """Sample power/energy state. Servers without a battery report unavailable."""
        override_percent = _env_optional_float("A2A_RESOURCE_ENERGY_PERCENT")
        if override_percent is not None:
            plugged_env = os.environ.get("A2A_RESOURCE_POWER_PLUGGED")
            power_plugged = (
                None if plugged_env is None else plugged_env.strip().lower() in {"1", "true", "yes"}
            )
            return {
                "available": True,
                "source": "env-override",
                "percent": override_percent,
                "power_plugged": power_plugged,
                "secs_left": None,
            }

        if psutil is None:
            return {"available": False, "reason": "psutil is not installed"}

        try:
            battery = psutil.sensors_battery()
        except Exception as exc:
            return {"available": False, "reason": str(exc)}

        if battery is None:
            return {
                "available": False,
                "reason": "no battery/energy sensor",
                "percent": None,
                "power_plugged": None,
            }

        secs_left = getattr(battery, "secsleft", None)
        if secs_left is not None and secs_left < 0:
            secs_left = None
        return {
            "available": True,
            "source": "psutil",
            "percent": battery.percent,
            "power_plugged": battery.power_plugged,
            "secs_left": secs_left,
        }

    def _sample_network(self) -> dict:
        """Sample bandwidth and link stability from network IO counters."""
        override_bandwidth = _env_optional_float("A2A_RESOURCE_BANDWIDTH_MBPS")
        override_stability = _env_optional_float("A2A_RESOURCE_LINK_STABILITY")
        if override_bandwidth is not None or override_stability is not None:
            return {
                "available": True,
                "source": "env-override",
                "send_kbps": None,
                "recv_kbps": None,
                "bandwidth_mbps": override_bandwidth,
                "link_stability": override_stability,
                "link_up": True,
            }

        if psutil is None:
            return {"available": False, "reason": "psutil is not installed"}

        try:
            counters = psutil.net_io_counters()
        except Exception as exc:
            return {"available": False, "reason": str(exc)}

        if counters is None:
            return {"available": False, "reason": "no network counters"}

        now = time.time()
        sent = int(counters.bytes_sent)
        recv = int(counters.bytes_recv)
        send_kbps = None
        recv_kbps = None
        bandwidth_mbps = None
        previous = self._last_net_sample
        self._last_net_sample = (now, sent, recv)
        if previous is not None:
            elapsed = now - previous[0]
            if elapsed > 0:
                # bits/sec -> kilobits/sec
                send_kbps = max(0.0, (sent - previous[1]) * 8 / 1000.0 / elapsed)
                recv_kbps = max(0.0, (recv - previous[2]) * 8 / 1000.0 / elapsed)
                bandwidth_mbps = (send_kbps + recv_kbps) / 1000.0

        total_packets = int(counters.packets_sent) + int(counters.packets_recv)
        total_errors = (
            int(counters.errin)
            + int(counters.errout)
            + int(counters.dropin)
            + int(counters.dropout)
        )
        link_stability = None
        if total_packets > 0:
            link_stability = max(0.0, 1.0 - (total_errors / total_packets))

        link_up = None
        try:
            stats = psutil.net_if_stats()
            link_up = any(
                nic.isup
                for name, nic in stats.items()
                if name.lower() not in {"lo", "lo0"}
            )
        except Exception:
            link_up = None

        return {
            "available": True,
            "source": "psutil",
            "send_kbps": send_kbps,
            "recv_kbps": recv_kbps,
            "bandwidth_mbps": bandwidth_mbps,
            "link_stability": link_stability,
            "link_up": link_up,
            "errors_total": total_errors,
            "packets_total": total_packets,
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

        return {
            "monitor_available": True,
            "node_online": bool(raw.get("node_online", True)),
            "sampled_at": utc_now_iso(),
            "system": system,
            "process": process,
            "gpu": self._normalize_gpu(raw.get("gpu")),
            "energy": self._normalize_energy(raw.get("energy")),
            "network": self._normalize_network(raw.get("network")),
        }

    @staticmethod
    def _normalize_gpu(gpu: Optional[dict]) -> dict:
        if not gpu:
            return {"available": False, "devices": []}
        normalized = dict(gpu)
        normalized["available"] = bool(gpu.get("available", False))
        normalized["gpu_percent"] = _round(gpu.get("gpu_percent"))
        normalized["memory_percent"] = _round(gpu.get("memory_percent"))
        return normalized

    @staticmethod
    def _normalize_energy(energy: Optional[dict]) -> dict:
        if not energy:
            return {"available": False, "percent": None, "power_plugged": None}
        normalized = dict(energy)
        normalized["available"] = bool(energy.get("available", False))
        normalized["percent"] = _round(energy.get("percent"))
        return normalized

    @staticmethod
    def _normalize_network(network: Optional[dict]) -> dict:
        if not network:
            return {"available": False}
        normalized = dict(network)
        normalized["available"] = bool(network.get("available", False))
        normalized["send_kbps"] = _round(network.get("send_kbps"))
        normalized["recv_kbps"] = _round(network.get("recv_kbps"))
        normalized["bandwidth_mbps"] = _round(network.get("bandwidth_mbps"))
        normalized["link_stability"] = _round(network.get("link_stability"), 4)
        return normalized

    def _unavailable_snapshot(self, reason: str) -> dict:
        return {
            "monitor_available": False,
            "node_online": False,
            "unavailable_reason": reason,
            "sampled_at": utc_now_iso(),
            "system": {},
            "process": {"pid": os.getpid()},
            "gpu": {"available": False, "devices": []},
            "energy": {"available": False, "percent": None, "power_plugged": None},
            "network": {"available": False},
        }
