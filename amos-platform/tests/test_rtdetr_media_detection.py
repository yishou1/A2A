from __future__ import annotations

import json
from pathlib import Path
import pytest

from amos_platform.algorithms.rtdetr_client import RTDETRClient
from amos_platform.api.app_factory import create_app
from amos_platform.api.routes import detection_routes


ROOT = Path(__file__).resolve().parents[1]


class FakeEngine:
    def __init__(self, media_cues: list[dict]):
        self._media_cues = media_cues

    def get_operator_state(self) -> dict:
        return {"scenario_story": {"media_cues": list(self._media_cues)}}


def released_png() -> dict:
    return {
        "media_id": "ASC-MEDIA-02",
        "capture_id": "ASC-CAPTURE-02",
        "sensor_instance_id": "UAV-ISR-01/EO-IR",
        "modality": "eo_ir",
        "mime_type": "image/png",
        "uri": "/static/assets/scenarios/air-space-sea-carrier-strike/02-uav-eo-airfield.png",
        "checksum": "sha256-example",
    }


@pytest.mark.parametrize("media_base", [None, "http://amos:5000/"])
def test_detection_route_only_forwards_server_released_media(monkeypatch, media_base) -> None:
    calls = []
    if media_base:
        monkeypatch.setenv("AMOS_PUBLIC_BASE_URL", media_base)
    else:
        monkeypatch.delenv("AMOS_PUBLIC_BASE_URL", raising=False)

    class FakeClient:
        def detect(self, media, image_url):
            calls.append((media, image_url))
            return {
                "request_id": "req-1",
                "trace_id": "trace-1",
                "algorithm_id": "battlefield_rtdetr_detector",
                "version": "1.0.0",
                "detections": [{"class_name": "airplane", "confidence": 0.91, "bbox": [1, 2, 30, 40]}],
                "count": 1,
                "runtime": {"mode": "real", "detector_model": "rtdetr-l.pt"},
                "usage": {"latency_ms": 42.5},
            }

    monkeypatch.setattr(detection_routes, "get_engine", lambda: FakeEngine([released_png()]))
    monkeypatch.setattr(detection_routes, "get_rtdetr_client", lambda: FakeClient())
    app = create_app()
    app.testing = True

    response = app.test_client().post("/api/v1/a2a/media/ASC-MEDIA-02/detect", json={})

    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["count"] == 1
    assert data["runtime"]["mode"] == "real"
    assert data["source_uri"] == released_png()["uri"]
    assert calls[0][0]["media_id"] == "ASC-MEDIA-02"
    assert calls[0][1] == (media_base or "http://localhost/").rstrip("/") + released_png()["uri"]


def test_detection_route_rejects_unreleased_and_non_raster_media(monkeypatch) -> None:
    svg = {"media_id": "VISIBLE-SVG", "mime_type": "image/svg+xml", "uri": "/visible.svg"}
    monkeypatch.setattr(detection_routes, "get_engine", lambda: FakeEngine([svg]))
    app = create_app()
    app.testing = True
    client = app.test_client()

    assert client.post("/api/v1/a2a/media/FUTURE-MEDIA/detect", json={}).status_code == 404
    assert client.post("/api/v1/a2a/media/VISIBLE-SVG/detect", json={}).status_code == 415


def test_detection_route_never_draws_mock_results_as_image_recognition(monkeypatch) -> None:
    class MockClient:
        def detect(self, media, image_url):
            return {"runtime": {"mode": "mock"}, "detections": [{"class_name": "vehicle"}], "count": 1}
    monkeypatch.setattr(detection_routes, "get_engine", lambda: FakeEngine([released_png()]))
    monkeypatch.setattr(detection_routes, "get_rtdetr_client", lambda: MockClient())
    response = create_app().test_client().post('/api/v1/a2a/media/ASC-MEDIA-02/detect', json={})
    assert response.status_code == 503
    assert 'vehicle' not in response.get_data(as_text=True)


def test_rtdetr_client_builds_image_uri_contract(monkeypatch) -> None:
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def read():
            return json.dumps({
                "ok": True,
                "request_id": "req-returned",
                "trace_id": "ASC-CAPTURE-02",
                "algorithm_id": "battlefield_rtdetr_detector",
                "version": "1.0.0",
                "outputs": {
                    "detections": [{"bbox": [10, 20, 30, 40]}],
                    "count": 1,
                    "runtime": {"mode": "real", "detector_model": "rtdetr-l.pt"},
                },
                "usage": {"latency_ms": 12.3},
            }).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr("amos_platform.algorithms.rtdetr_client.urlopen", fake_urlopen)
    result = RTDETRClient("http://detector:9020/", timeout_sec=9).detect(
        released_png(),
        "http://amos:5000/static/frame.png",
    )

    assert captured["url"] == "http://detector:9020/predict"
    assert captured["timeout"] == 9
    frame = captured["body"]["inputs"]["frames"][0]
    assert frame["payload"]["image_uri"] == "http://amos:5000/static/frame.png"
    assert frame["metadata"]["media_id"] == "ASC-MEDIA-02"
    assert result["count"] == 1
    assert result["runtime"]["mode"] == "real"


def test_dashboard_contains_rtdetr_overlay_controls() -> None:
    html = (ROOT / "templates" / "dashboard.html").read_text(encoding="utf-8")
    javascript = (ROOT / "static" / "js" / "app" / "platform.js").read_text(encoding="utf-8")
    api = (ROOT / "static" / "js" / "api" / "platform-api.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "platform.css").read_text(encoding="utf-8")

    assert 'id="media-detect-button"' in html
    assert 'id="media-detection-overlay"' in html
    assert "function renderDetectionOverlay" in javascript
    assert "API.detectMedia(mediaId)" in javascript
    assert "a2a/media/" in api and "/detect" in api
    assert ".media-detection-box" in css
