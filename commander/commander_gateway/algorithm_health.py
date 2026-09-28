"""Probe only registered local algorithm health endpoints from the core container."""

from __future__ import annotations

from urllib.parse import quote, urlsplit

import requests

from commander_gateway.errors import GatewayError


ALGOLIB_URL = "http://127.0.0.1:8088"


def _read_json(session: requests.Session, url: str, service: str) -> dict:
    try:
        response = session.get(url, timeout=2, allow_redirects=False)
        response.raise_for_status()
        if response.status_code != 200:
            raise GatewayError(
                "ALGORITHM_HEALTH_UNAVAILABLE",
                f"{service} returned an unexpected status",
                503,
                True,
            )
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise GatewayError(
            "ALGORITHM_HEALTH_UNAVAILABLE",
            f"{service} health lookup failed",
            503,
            True,
        ) from exc
    if not isinstance(payload, dict):
        raise GatewayError(
            "ALGORITHM_HEALTH_INVALID_RESPONSE",
            f"{service} returned invalid health data",
            502,
        )
    return payload


def probe_algorithm_health(algorithm_id: str, version: str, backend_type: str) -> dict:
    """Resolve an active registry card, then visit its local algorithm health URL.

    The caller supplies registry identity only. The upstream URL comes from the
    active AlgoLib card and must remain on the dedicated local algorithm ports.
    """
    allowed = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")
    for value in (algorithm_id, version, backend_type):
        if not value or len(value) > 128 or any(char not in allowed for char in value):
            raise GatewayError("INVALID_ALGORITHM_ID", "invalid algorithm identity", 400)

    with requests.Session() as session:
        session.trust_env = False
        listing = _read_json(session, f"{ALGOLIB_URL}/algorithms?active_only=true", "AlgoLib")
        if not any(
            item.get("algorithm_id") == algorithm_id
            and item.get("version") == version
            and item.get("backend_type") == backend_type
            for item in listing.get("algorithms", [])
            if isinstance(item, dict)
        ):
            raise GatewayError("ALGORITHM_NOT_ACTIVE", "algorithm is not active", 404)

        key = "/".join(quote(part, safe="") for part in (algorithm_id, version, backend_type))
        detail = _read_json(session, f"{ALGOLIB_URL}/algorithms/{key}", "AlgoLib")
        entry = detail.get("entry") if isinstance(detail.get("entry"), dict) else {}
        card = entry.get("card") if isinstance(entry.get("card"), dict) else {}
        machine = card.get("machine_spec") if isinstance(card.get("machine_spec"), dict) else {}
        runtime = machine.get("runtime") if isinstance(machine.get("runtime"), dict) else {}
        endpoint = str(runtime.get("health_endpoint") or "")
        parsed = urlsplit(endpoint)
        try:
            port = parsed.port
        except ValueError as exc:
            raise GatewayError("INVALID_ALGORITHM_HEALTH_URL", "invalid algorithm health URL", 400) from exc
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost"}
            or port is None
            or not 9000 <= port <= 9099
            or not parsed.path.endswith("/health")
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise GatewayError(
                "INVALID_ALGORITHM_HEALTH_URL",
                "algorithm health URL is outside the local runtime allowlist",
                400,
            )
        return _read_json(session, endpoint, "Algorithm")
