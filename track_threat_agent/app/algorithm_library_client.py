"""Optional Nacos-discovered client for the shared track-threat algorithm library.

The Track Threat Agent owns long-lived track state.  The shared algorithm
library is therefore used as a preferred stateless/compute provider while the
Agent keeps a local implementation as the continuity fallback.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict
from urllib import parse, request


Transport = Callable[[str, str, Dict[str, Any] | None, float], Dict[str, Any]]


@dataclass(frozen=True)
class AlgorithmLibrarySettings:
    enabled: bool = False
    base_url: str = ""
    nacos_server: str = "127.0.0.1:8848"
    nacos_namespace: str = "public"
    nacos_group: str = "DEFAULT_GROUP"
    service_name: str = "track-threat-algorithms"
    timeout_s: float = 3.0
    refresh_s: float = 30.0

    @classmethod
    def from_env(cls) -> "AlgorithmLibrarySettings":
        return cls(
            enabled=os.getenv("ALGORITHM_LIBRARY_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
            base_url=os.getenv("ALGORITHM_LIBRARY_BASE_URL", "").strip(),
            nacos_server=os.getenv("NACOS_SERVER", "127.0.0.1:8848").strip(),
            nacos_namespace=os.getenv("NACOS_NAMESPACE", "public").strip(),
            nacos_group=os.getenv("NACOS_GROUP", "DEFAULT_GROUP").strip(),
            service_name=os.getenv("ALGORITHM_LIBRARY_SERVICE_NAME", "track-threat-algorithms").strip(),
            timeout_s=max(0.1, float(os.getenv("ALGORITHM_LIBRARY_TIMEOUT_S", "3"))),
            refresh_s=max(0.0, float(os.getenv("ALGORITHM_LIBRARY_REFRESH_S", "30"))),
        )


@dataclass
class RemoteAlgorithmResult:
    algorithm_id: str
    ok: bool
    outputs: Dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    source: str = ""
    endpoint: str = ""
    error: str | None = None

    @classmethod
    def ok_result(
        cls,
        algorithm_id: str,
        outputs: Dict[str, Any],
        *,
        latency_ms: float = 0.0,
        source: str = "",
        endpoint: str = "",
    ) -> "RemoteAlgorithmResult":
        return cls(
            algorithm_id=algorithm_id,
            ok=True,
            outputs=dict(outputs or {}),
            latency_ms=float(latency_ms or 0.0),
            source=source,
            endpoint=endpoint,
        )

    @classmethod
    def failure(
        cls,
        algorithm_id: str,
        error: str,
        *,
        source: str = "",
        endpoint: str = "",
    ) -> "RemoteAlgorithmResult":
        return cls(
            algorithm_id=algorithm_id,
            ok=False,
            source=source,
            endpoint=endpoint,
            error=error,
        )


class AlgorithmLibraryClient:
    """Discovers the shared service through Nacos and calls its HTTP APIs.

    A configured base URL is useful for local integration tests and takes
    priority over Nacos.  No exception leaves this boundary: callers receive a
    failure result and use their existing local algorithm implementation.
    """

    def __init__(
        self,
        settings: AlgorithmLibrarySettings | None = None,
        *,
        transport: Transport | None = None,
    ) -> None:
        self.settings = settings or AlgorithmLibrarySettings.from_env()
        self._transport = transport or self._http_json
        self._cached_base_url = ""
        self._cached_source = ""
        self._cache_expires_at = 0.0
        self._last_error = ""

    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "service_name": self.settings.service_name,
            "configured_base_url": self.settings.base_url or None,
            "resolved_base_url": self._cached_base_url or None,
            "resolution_source": self._cached_source or None,
            "last_error": self._last_error or None,
        }

    def invoke(
        self,
        algorithm_id: str,
        inputs: Dict[str, Any],
        params: Dict[str, Any] | None = None,
        *,
        trace_id: str = "",
    ) -> RemoteAlgorithmResult:
        if not self.enabled:
            return RemoteAlgorithmResult.failure(algorithm_id, "algorithm_library_disabled")

        base_url, source = self._resolve_base_url()
        if not base_url:
            return RemoteAlgorithmResult.failure(
                algorithm_id,
                self._last_error or "algorithm_library_unavailable",
                source=source,
            )

        endpoint = f"{base_url.rstrip('/')}/{algorithm_id}/predict"
        payload = {
            "request_id": f"track-threat-{int(time.time() * 1000)}",
            "trace_id": trace_id,
            "algorithm_id": algorithm_id,
            "version": "1.0.0",
            "inputs": dict(inputs or {}),
            "params": dict(params or {}),
        }
        try:
            response = self._transport("POST", endpoint, payload, self.settings.timeout_s)
        except Exception as exc:
            error = f"http_error:{type(exc).__name__}:{exc}"
            self._last_error = error
            return RemoteAlgorithmResult.failure(algorithm_id, error, source=source, endpoint=endpoint)

        if not bool(response.get("ok")):
            error_body = response.get("error") or {}
            error = str(error_body.get("message") or error_body.get("code") or "remote_algorithm_failed")
            self._last_error = error
            return RemoteAlgorithmResult.failure(algorithm_id, error, source=source, endpoint=endpoint)

        usage = response.get("usage") or {}
        self._last_error = ""
        return RemoteAlgorithmResult.ok_result(
            algorithm_id,
            response.get("outputs") or {},
            latency_ms=float(usage.get("latency_ms") or 0.0),
            source=source,
            endpoint=endpoint,
        )

    def _resolve_base_url(self) -> tuple[str, str]:
        if self.settings.base_url:
            return self.settings.base_url.rstrip("/"), "configured_base_url"
        if self._cached_base_url and time.monotonic() < self._cache_expires_at:
            return self._cached_base_url, self._cached_source
        return self._discover_nacos_instance()

    def _discover_nacos_instance(self) -> tuple[str, str]:
        server = self.settings.nacos_server.strip()
        if not server:
            self._last_error = "nacos_server_missing"
            return "", "nacos_discovery"
        if not server.startswith(("http://", "https://")):
            server = f"http://{server}"
        query: Dict[str, str] = {
            "serviceName": self.settings.service_name,
            "groupName": self.settings.nacos_group,
        }
        if self.settings.nacos_namespace and self.settings.nacos_namespace != "public":
            query["namespaceId"] = self.settings.nacos_namespace
        endpoint = f"{server.rstrip('/')}/nacos/v1/ns/instance/list?{parse.urlencode(query)}"
        try:
            payload = self._transport("GET", endpoint, None, self.settings.timeout_s)
        except Exception as exc:
            self._last_error = f"nacos_discovery_error:{type(exc).__name__}:{exc}"
            return "", "nacos_discovery"

        for host in payload.get("hosts") or []:
            if not host.get("enabled", True) or not host.get("healthy", False):
                continue
            metadata = dict(host.get("metadata") or {})
            if metadata.get("owner_scope") != "track_threat_agent":
                continue
            base_url = str(metadata.get("base_url") or "").strip()
            if not base_url:
                ip = str(host.get("ip") or "").strip()
                port = host.get("port")
                if ip and port:
                    base_url = f"http://{ip}:{port}"
            if not base_url:
                continue
            self._cached_base_url = base_url.rstrip("/")
            self._cached_source = "nacos_discovery"
            self._cache_expires_at = time.monotonic() + self.settings.refresh_s
            self._last_error = ""
            return self._cached_base_url, self._cached_source

        self._last_error = "no_healthy_track_threat_algorithm_service"
        return "", "nacos_discovery"

    @staticmethod
    def _http_json(method: str, url: str, payload: Dict[str, Any] | None, timeout_s: float) -> Dict[str, Any]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request_object = request.Request(url, data=body, headers=headers, method=method)
        with request.urlopen(request_object, timeout=timeout_s) as response:  # nosec B310 - URL is explicit config/Nacos discovery
            return json.loads(response.read().decode("utf-8"))
