from __future__ import annotations

from amos_platform.api.app_factory import create_app


def test_dashboard_links_to_default_algorithm_console() -> None:
    response = create_app().test_client().get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="open-algorithm-console"' in html
    assert 'href="http://127.0.0.1:5173/algorithms"' in html
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
