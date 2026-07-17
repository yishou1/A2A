from __future__ import annotations

from typing import Any

import requests

from commander_gateway.config import GatewayConfig
from commander_gateway.errors import UpstreamError


class _JsonHttpClient:
    def __init__(self, base_url: str, timeout: float, token: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token = token

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        service: str,
        json_body: dict | None = None,
        params: dict | None = None,
    ) -> Any:
        try:
            response = requests.request(
                method,
                f"{self.base_url}{path}",
                headers=self._headers(),
                json=json_body,
                params=params,
                timeout=self.timeout,
            )
        except requests.Timeout as exc:
            raise UpstreamError(
                f"{service}_TIMEOUT",
                f"{service.title()} request timed out",
                504,
                True,
            ) from exc
        except requests.RequestException as exc:
            raise UpstreamError(
                f"{service}_UNAVAILABLE",
                f"{service.title()} request failed",
                503,
                True,
            ) from exc

        if not 200 <= response.status_code < 300:
            if response.status_code == 404:
                code = f"{service}_NOT_FOUND"
                retriable = False
            elif response.status_code == 409:
                code = f"{service}_CONFLICT"
                retriable = False
            elif response.status_code >= 500:
                code = f"{service}_UNAVAILABLE"
                retriable = True
            else:
                code = f"{service}_REJECTED"
                retriable = False
            try:
                error_payload = response.json()
                message = str(error_payload.get("detail") or error_payload.get("error"))
            except (ValueError, AttributeError):
                message = response.text.strip()
            raise UpstreamError(
                code,
                message or f"{service.title()} request failed",
                response.status_code,
                retriable,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise UpstreamError(
                f"{service}_INVALID_RESPONSE",
                f"{service.title()} returned invalid JSON",
                502,
                True,
            ) from exc


class AmosClient(_JsonHttpClient):
    def __init__(self, config: GatewayConfig) -> None:
        super().__init__(config.amos_base_url, config.request_timeout_sec)

    @staticmethod
    def _data(payload: Any) -> Any:
        if not isinstance(payload, dict) or "data" not in payload:
            raise UpstreamError(
                "AMOS_INVALID_RESPONSE", "AMOS response has no data envelope", 502, True
            )
        return payload["data"]

    def get_status(self) -> dict:
        payload = self._request("GET", "/api/v1/status", service="AMOS")
        data = self._data(payload)
        if not isinstance(data, dict):
            raise UpstreamError("AMOS_INVALID_RESPONSE", "AMOS status is invalid", 502, True)
        return data

    def get_snapshot(self) -> dict:
        payload = self._request("GET", "/api/v1/sim/snapshot", service="AMOS")
        data = self._data(payload)
        if not isinstance(data, dict):
            raise UpstreamError("AMOS_INVALID_RESPONSE", "AMOS snapshot is invalid", 502, True)
        return data

    def get_events(self, after_sequence: int = 0) -> list:
        payload = self._request(
            "GET",
            "/api/v1/sim/events",
            service="AMOS",
            params={"after_sequence": after_sequence},
        )
        data = self._data(payload)
        if not isinstance(data, list):
            raise UpstreamError("AMOS_INVALID_RESPONSE", "AMOS events are invalid", 502, True)
        return data


class CommanderClient(_JsonHttpClient):
    def __init__(self, config: GatewayConfig) -> None:
        super().__init__(
            config.commander_base_url,
            config.request_timeout_sec,
            config.commander_token,
        )

    def health(self) -> dict:
        return self._request("GET", "/health", service="COMMANDER")

    def submit_workflow(self, payload: dict) -> dict:
        return self._request(
            "POST", "/workflows", service="COMMANDER", json_body=payload
        )

    def get_workflow(self, workflow_id: str) -> dict:
        return self._request(
            "GET", f"/workflows/{workflow_id}", service="COMMANDER"
        )

    def get_work_list(self, workflow_id: str) -> dict:
        return self._request(
            "GET", f"/workflows/{workflow_id}/work-list", service="COMMANDER"
        )

    def get_trace(self, workflow_id: str) -> dict:
        return self._request(
            "GET", f"/workflows/{workflow_id}/trace", service="COMMANDER"
        )

    def resume_workflow(self, workflow_id: str, payload: dict) -> dict:
        return self._request(
            "POST",
            f"/workflows/{workflow_id}/resume",
            service="COMMANDER",
            json_body=payload,
        )
