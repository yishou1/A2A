from __future__ import annotations

from pathlib import Path

from amos_platform.agents.commander_bridge import CommanderBridge
from amos_platform.data.operational_catalog import algorithm_classes, operational_functions


ROOT = Path(__file__).resolve().parents[1]


def test_function_point_catalog_declares_workflow_primary_implementations() -> None:
    rows = {item["function_id"]: item for item in operational_functions()}

    assert rows["KC-04"]["primary_algorithm_ids"] == ["mission_feature_adapter"]
    assert rows["KC-23"]["primary_algorithm_ids"] == ["execution_control_planner"]
    assert rows["KC-28"]["primary_algorithm_ids"] == ["mission_completion_scorer"]


def test_large_language_model_declares_qwen_primary_implementation() -> None:
    rows = {item["requirement_id"]: item for item in algorithm_classes()}

    assert rows["M09"]["primary_algorithm_ids"] == ["qwen3-1.7B"]


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
                        "summary": "多目标跟踪与定位",
                        "agent_card": {"summary": "跟踪算法服务"},
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
    assert catalog["algorithms"][0]["summary"] == "多目标跟踪与定位"
    assert catalog["algorithms"][0]["agent_card"]["summary"] == "跟踪算法服务"


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


def test_container_catalog_checks_loopback_algorithm_through_gateway(monkeypatch) -> None:
    monkeypatch.setenv("A2A_CONTAINER_MODE", "1")
    monkeypatch.setenv("ALGOLIB_BASE_URL", "http://a2a-core:8088")
    bridge = CommanderBridge(gateway_url="http://a2a-core:8030")
    health_url = (
        "http://a2a-core:8030/gateway/v1/algorithms/"
        "ready_algorithm/1.0.0/python_http_service/health"
    )

    def fake_fetch(url: str, timeout: float = 2.0) -> dict:
        if url == "http://a2a-core:8088/health":
            return {"ok": True, "status": "ready"}
        if url == "http://a2a-core:8088/algorithms?active_only=true":
            return {"algorithms": [{
                "algorithm_id": "ready_algorithm",
                "version": "1.0.0",
                "backend_type": "python_http_service",
            }]}
        if url.endswith("/algorithms/ready_algorithm/1.0.0/python_http_service"):
            return {"entry": {"card": {"machine_spec": {"runtime": {
                "health_endpoint": "http://127.0.0.1:9010/health",
            }}}}}
        if url == health_url:
            return {"ok": True, "status": "ready"}
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(bridge, "_fetch_json", fake_fetch)

    catalog = bridge.algorithm_catalog(force=True)

    assert catalog["status"] == "ready"
    assert catalog["runnable_count"] == 1


def test_algorithm_panel_only_lists_onnx_packages_in_onnx_group() -> None:
    panels = (ROOT / "static/js/panels/platform-panels.js").read_text(encoding="utf-8")

    assert 'renderGroup("ONNX 模型包", onnxRows)' in panels
    assert 'renderGroup("其他实现包"' not in panels
    assert "当前不可执行" not in panels
    assert "运行时不可用" not in panels
