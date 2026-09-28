from __future__ import annotations

import pytest

from commander_gateway import algorithm_health
from commander_gateway.errors import GatewayError


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload
        self.status_code = 200

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self.payload


class FakeSession:
    def __init__(self, responses: dict[str, dict]):
        self.responses = responses
        self.visited: list[str] = []
        self.trust_env = True

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *_args: object) -> None:
        pass

    def get(self, url: str, *, timeout: int, allow_redirects: bool) -> FakeResponse:
        assert self.trust_env is False
        assert timeout == 2
        assert allow_redirects is False
        self.visited.append(url)
        return FakeResponse(self.responses[url])


def test_probe_uses_registered_local_health_endpoint(monkeypatch) -> None:
    base = algorithm_health.ALGOLIB_URL
    health_url = "http://127.0.0.1:9038/trajectory_predictor/health"
    fake = FakeSession({
        f"{base}/algorithms?active_only=true": {
            "algorithms": [{
                "algorithm_id": "trajectory_predictor",
                "version": "1.0.0",
                "backend_type": "python_http_service",
            }],
        },
        f"{base}/algorithms/trajectory_predictor/1.0.0/python_http_service": {
            "entry": {"card": {"machine_spec": {"runtime": {
                "health_endpoint": health_url,
            }}}},
        },
        health_url: {"ok": True, "status": "ready"},
    })
    monkeypatch.setattr(algorithm_health.requests, "Session", lambda: fake)

    result = algorithm_health.probe_algorithm_health(
        "trajectory_predictor", "1.0.0", "python_http_service"
    )

    assert result == {"ok": True, "status": "ready"}
    assert fake.visited[-1] == health_url


def test_probe_rejects_non_algorithm_local_port(monkeypatch) -> None:
    base = algorithm_health.ALGOLIB_URL
    fake = FakeSession({
        f"{base}/algorithms?active_only=true": {
            "algorithms": [{
                "algorithm_id": "bad_card",
                "version": "1.0.0",
                "backend_type": "python_http_service",
            }],
        },
        f"{base}/algorithms/bad_card/1.0.0/python_http_service": {
            "entry": {"card": {"machine_spec": {"runtime": {
                "health_endpoint": "http://127.0.0.1:8021/health",
            }}}},
        },
    })
    monkeypatch.setattr(algorithm_health.requests, "Session", lambda: fake)

    with pytest.raises(GatewayError, match="allowlist"):
        algorithm_health.probe_algorithm_health("bad_card", "1.0.0", "python_http_service")
    assert len(fake.visited) == 2
