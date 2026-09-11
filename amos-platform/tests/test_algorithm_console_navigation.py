from __future__ import annotations

import json
from pathlib import Path

from amos_platform.api import algolib_console
from amos_platform.api.app_factory import create_app


def test_dashboard_links_to_default_algorithm_console() -> None:
    response = create_app().test_client().get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="open-algorithm-console"' in html
    assert 'href="/algolib/algorithms"' in html
    assert 'target="_blank"' in html
    assert 'rel="noopener noreferrer"' in html


def test_dashboard_accepts_configured_algorithm_console_url(monkeypatch) -> None:
    monkeypatch.setenv(
        "ALGOLIB_CONSOLE_URL",
        "http://127.0.0.1:5000/algolib/algorithms",
    )

    response = create_app().test_client().get("/")

    assert response.status_code == 200
    assert (
        'href="http://127.0.0.1:5000/algolib/algorithms"'
        in response.get_data(as_text=True)
    )


def test_algorithm_cards_link_to_same_origin_management_details() -> None:
    panels = (
        Path(__file__).resolve().parents[1]
        / "static"
        / "js"
        / "panels"
        / "platform-panels.js"
    ).read_text(encoding="utf-8")

    assert '"/algolib/algorithms/"' in panels
    assert 'class="algorithm-console-detail-link"' in panels
    assert 'rel="noopener noreferrer"' in panels


def test_amos_serves_algorithm_console_spa_routes(tmp_path, monkeypatch) -> None:
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<main>AlgoLib console</main>", encoding="utf-8")
    (assets / "app.js").write_text("window.algolibReady = true", encoding="utf-8")
    monkeypatch.setenv("ALGOLIB_WEB_DIST", str(dist))

    client = create_app().test_client()

    detail_response = client.get("/algolib/algorithms/example/1.0.0/onnx")
    asset_response = client.get("/algolib/assets/app.js")
    assert detail_response.status_code == 200
    assert "AlgoLib console" in detail_response.get_data(as_text=True)
    assert asset_response.status_code == 200
    assert "algolibReady" in asset_response.get_data(as_text=True)


def test_amos_proxies_algorithm_api_requests(monkeypatch) -> None:
    captured = {}

    class FakeUpstream:
        status = 200
        headers = {"Content-Type": "application/json", "X-Trace-ID": "trace-test"}

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        @staticmethod
        def read() -> bytes:
            return json.dumps({"ok": True, "count": 1}).encode("utf-8")

    def fake_urlopen(upstream_request, timeout):
        captured["url"] = upstream_request.full_url
        captured["method"] = upstream_request.get_method()
        captured["body"] = upstream_request.data
        captured["timeout"] = timeout
        return FakeUpstream()

    monkeypatch.setenv("ALGOLIB_API_URL", "http://algolib.internal:8088/")
    monkeypatch.setattr(algolib_console.urllib.request, "urlopen", fake_urlopen)

    response = create_app().test_client().post(
        "/algolib-api/run?dry_run=true",
        json={"algorithm_id": "example"},
        headers={"X-Trace-ID": "trace-test"},
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "count": 1}
    assert response.headers["X-Trace-ID"] == "trace-test"
    assert captured == {
        "url": "http://algolib.internal:8088/run?dry_run=true",
        "method": "POST",
        "body": b'{"algorithm_id": "example"}',
        "timeout": 30.0,
    }
