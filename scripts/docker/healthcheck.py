"""Container health checks without dependencies outside Python's standard library."""

from __future__ import annotations

import socket
import sys
import json
import os
from urllib.request import urlopen


def probe_port(port: int) -> None:
    with socket.create_connection(("127.0.0.1", port), timeout=2):
        pass


def probe_http(url: str) -> None:
    with urlopen(url, timeout=3) as response:
        if response.status != 200:
            raise RuntimeError(f"{url} returned {response.status}")


def probe_gateway_ready() -> None:
    with urlopen("http://127.0.0.1:8030/gateway/v1/health", timeout=10) as response:
        payload = json.load(response)
        if response.status != 200 or payload.get("status") != "ok":
            raise RuntimeError(f"Gateway is not ready: {payload}")


def probe_amos_backend(host: str = "127.0.0.1") -> None:
    with urlopen(f"http://{host}:5000/api/v1/a2a/backend/health", timeout=10) as response:
        payload = json.load(response)
        status = (payload.get("data") or {}).get("status")
        if response.status != 200 or status != "ok":
            raise RuntimeError(f"AMOS A2A backend is not ready: {payload}")


def probe_synapserag(host: str = "amos") -> None:
    with urlopen(f"http://{host}:5000/synapserag-api/health", timeout=15) as response:
        payload = json.load(response)
        if response.status != 200 or payload.get("status") != "ok" or payload.get("indexed") is not True:
            raise RuntimeError(f"SynapseRAG is not ready: {payload}")


def probe_core() -> None:
    for port in (8088, 8021, 8030, 8102, 10200, 10201, 10202, 10203, 10204, 10205):
        probe_port(port)
    probe_http("http://127.0.0.1:8088/health")
    probe_http("http://127.0.0.1:8021/health")
    probe_http("http://127.0.0.1:8030/gateway/v1/ready")
    for port in (8102, 10200, 10201, 10202, 10203, 10204, 10205):
        probe_http(f"http://127.0.0.1:{port}/health")


def probe_rtdetr() -> None:
    endpoint = os.environ.get("AMOS_RTDETR_ENDPOINT", "http://a2a-core:9020").rstrip("/")
    with urlopen(endpoint + "/health", timeout=10) as response:
        payload = json.load(response)
        if (response.status != 200 or payload.get("ok") is not True
                or payload.get("model_loaded") is not True
                or payload.get("algorithm_id") != "battlefield_rtdetr_detector"):
            raise RuntimeError(f"RT-DETR is not ready at {endpoint}: {payload}")
    if os.environ.get("RTDETR_USE_MOCK") in {"0", "false", "False"}:
        with urlopen(endpoint + "/metadata", timeout=10) as response:
            metadata = json.load(response)
            if metadata.get("inference_mode") != "real" or metadata.get("num_classes") != 97:
                raise RuntimeError("RT-DETR is not running the required real 97-class model")


def main() -> None:
    target = sys.argv[1]
    if target == "core":
        probe_core()
    elif target == "amos":
        probe_http("http://127.0.0.1:5000/api/v1/scenarios")
        probe_amos_backend()
        probe_http("http://127.0.0.1:5000/algolib-api/health")
        probe_rtdetr()
    elif target == "full":
        probe_core()
        probe_gateway_ready()
        probe_http("http://amos:5000/")
        probe_amos_backend("amos")
        probe_http("http://amos:5000/algolib/")
        probe_http("http://amos:5000/algolib-api/health")
        probe_synapserag()
        probe_rtdetr()
    else:
        raise ValueError(f"unknown healthcheck target: {target}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
