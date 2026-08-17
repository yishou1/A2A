from __future__ import annotations

from amos_platform.agents.commander_bridge import CommanderBridge


def test_algorithm_catalog_only_counts_healthy_active_runtimes(monkeypatch) -> None:
    bridge = CommanderBridge()

    def fake_fetch(url: str, timeout: float = 2.0) -> dict:
        if url.endswith("/health") and ":8088" in url:
            return {"ok": True, "status": "ready"}
        if url.endswith("/algorithms?active_only=true"):
            return {
                "algorithms": [
                    {
                        "algorithm_id": "ready_algorithm",
                        "display_name": "Ready Algorithm",
                        "version": "1.0.0",
                        "backend_type": "python_http_service",
                        "task_family": "tracking",
                        "capabilities": ["tracking"],
                    },
                    {
                        "algorithm_id": "offline_algorithm",
                        "display_name": "Offline Algorithm",
                        "version": "1.0.0",
                        "backend_type": "python_http_service",
                        "task_family": "planning",
                        "capabilities": ["planning"],
                    },
                ]
            }
        if "/algorithms/ready_algorithm/" in url:
            return {"entry": {"card": {"machine_spec": {"runtime": {
                "health_endpoint": "http://runtime/ready"
            }}}}}
        if "/algorithms/offline_algorithm/" in url:
            return {"entry": {"card": {"machine_spec": {"runtime": {
                "health_endpoint": "http://runtime/offline"
            }}}}}
        if url == "http://runtime/ready":
            return {"ok": True, "status": "ready", "model_loaded": True}
        if url == "http://runtime/offline":
            return {"ok": False, "status": "error"}
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(bridge, "_fetch_json", fake_fetch)

    catalog = bridge.algorithm_catalog(force=True)

    assert catalog["status"] == "degraded"
    assert catalog["active_count"] == 2
    assert catalog["runnable_count"] == 1
    assert catalog["unavailable_count"] == 1
    assert [item["algorithm_id"] for item in catalog["algorithms"] if item["runtime_status"] == "ready"] == [
        "ready_algorithm"
    ]
    assert catalog["algorithms"][0]["model_loaded"] is True


def test_algorithm_catalog_accepts_flat_algolib_gateway_shape(monkeypatch) -> None:
    bridge = CommanderBridge()

    def fake_fetch(url: str, timeout: float = 2.0) -> dict:
        if url.endswith("/health") and ":8088" in url:
            return {"ok": True, "status": "ready"}
        if url.endswith("/algorithms?active_only=true"):
            return {
                "algorithms": [
                    {
                        "algorithm_id": "flat_algorithm",
                        "display_name": "Flat Algorithm",
                        "version": "1.0.0",
                        "backend_type": "python_http_service",
                        "task_family": "planning",
                        "capabilities": ["planning"],
                        "predict_endpoint": "http://runtime/flat/predict",
                    },
                ]
            }
        if "/algorithms/flat_algorithm/" in url:
            return {
                "algorithm_id": "flat_algorithm",
                "version": "1.0.0",
                "backend_type": "python_http_service",
                "predict_endpoint": "http://runtime/flat/predict",
            }
        if url == "http://runtime/flat/health":
            return {"ok": True, "status": "ready", "model_loaded": True}
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(bridge, "_fetch_json", fake_fetch)

    catalog = bridge.algorithm_catalog(force=True)

    assert catalog["status"] == "ready"
    assert catalog["active_count"] == 1
    assert catalog["runnable_count"] == 1
    assert catalog["algorithms"][0]["runtime_status"] == "ready"
    assert catalog["algorithms"][0]["predict_endpoint"] == "http://runtime/flat/predict"

