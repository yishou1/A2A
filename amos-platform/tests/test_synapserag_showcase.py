from __future__ import annotations

import json
from pathlib import Path

from amos_platform.api import synapserag_proxy
from amos_platform.api.app_factory import create_app


ROOT = Path(__file__).resolve().parents[1]


class FakeUpstream:
    status = 200
    headers = {
        "Content-Type": "application/json",
        "Content-Disposition": 'inline; filename="evidence.pdf"',
    }

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    @staticmethod
    def read() -> bytes:
        return json.dumps({"status": "success", "traces": []}).encode("utf-8")


def test_synapserag_proxy_is_fixed_target_read_only_and_server_authenticated(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(upstream_request, timeout):
        captured["url"] = upstream_request.full_url
        captured["method"] = upstream_request.get_method()
        captured["authorization"] = upstream_request.headers.get("Authorization")
        captured["timeout"] = timeout
        return FakeUpstream()

    monkeypatch.setenv("SYNAPSERAG_BASE_URL", "http://synapse.internal:8000/")
    monkeypatch.setenv("SYNAPSERAG_API_TOKEN", "server-only-token")
    monkeypatch.setenv("SYNAPSERAG_TIMEOUT_SECONDS", "42")
    monkeypatch.setattr(synapserag_proxy.urllib.request, "urlopen", fake_urlopen)

    response = create_app().test_client().get(
        "/synapserag-api/retrieval-traces?limit=12&workflow_id=wf-1&unexpected=discarded",
        headers={"Authorization": "Bearer browser-token"},
    )

    assert response.status_code == 200
    assert response.get_json() == {"status": "success", "traces": []}
    assert response.headers["Content-Disposition"] == 'inline; filename="evidence.pdf"'
    assert captured == {
        "url": "http://synapse.internal:8000/api/retrieval-traces?workflow_id=wf-1&limit=12",
        "method": "GET",
        "authorization": "Bearer server-only-token",
        "timeout": 42.0,
    }


def test_synapserag_proxy_rejects_unsafe_identifiers(monkeypatch) -> None:
    monkeypatch.setattr(
        synapserag_proxy.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not proxy")),
    )

    response = create_app().test_client().get(
        "/synapserag-api/retrieval-traces/not%20a%20trace"
    )

    assert response.status_code == 400


def test_dashboard_loads_trace_sidebar_and_graph_overlay_protocol() -> None:
    response = create_app().test_client().get("/")
    html = response.get_data(as_text=True)
    graph_html = (
        ROOT / "static" / "knowledge-graph" / "roe-knowledge-graph.html"
    ).read_text(encoding="utf-8")
    controller = (
        ROOT / "static" / "js" / "knowledge" / "synapserag-traces.js"
    ).read_text(encoding="utf-8")

    assert response.status_code == 200
    assert 'id="synapse-trace-list"' in html
    assert 'data-synapse-view="skeleton"' in html
    assert 'data-synapse-view="candidates"' in html
    assert "/static/js/knowledge/synapserag-traces.js" in html
    assert 'event.data.type === "synapserag:apply-overlay"' in graph_html
    assert 'synapserag:ready' in graph_html
    assert 'window.parent.postMessage' in graph_html
    assert '"/retrieval-traces/"' in controller
    assert '"/preview"' in controller
    assert 'textContent' in controller
