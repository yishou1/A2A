"""Idempotently pull and probe the exact Ollama model required by A2A."""

from __future__ import annotations

import json
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434").rstrip("/")
MODELS = tuple(
    model.strip()
    for model in os.environ.get(
        "OLLAMA_MODELS",
        os.environ.get("OLLAMA_MODEL", "qwen3:1.7b"),
    ).split(",")
    if model.strip()
)


def request_json(path: str, payload: dict | None = None, timeout: float = 30) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        BASE_URL + path,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def wait_for_server(timeout: float = 180) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            request_json("/api/tags", timeout=5)
            return
        except (URLError, HTTPError, TimeoutError, ValueError):
            time.sleep(2)
    raise RuntimeError("Ollama API did not become ready")


def model_present(model: str) -> bool:
    tags = request_json("/api/tags")
    return any(item.get("name") == model for item in tags.get("models", []))


def pull_model(model: str, *, deadline: float) -> None:
    request = Request(
        BASE_URL + "/api/pull",
        data=json.dumps({"model": model, "stream": True}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    last_status = ""
    last_bucket = -1
    last_progress = -1
    last_progress_at = time.monotonic()
    stall_timeout = max(30, float(os.environ.get("QWEN_STALL_TIMEOUT_SECONDS", "180")))
    with urlopen(request, timeout=min(120, max(1, deadline - time.monotonic()))) as response:
        for line in response:
            now = time.monotonic()
            if now >= deadline:
                raise TimeoutError(f"Ollama model download exceeded {os.environ.get('QWEN_DOWNLOAD_TIMEOUT_SECONDS', '3600')} seconds")
            if not line.strip():
                continue
            progress = json.loads(line)
            if progress.get("error"):
                raise RuntimeError(f"Ollama pull failed: {progress['error']}")
            status = progress.get("status", "")
            completed = progress.get("completed")
            total = progress.get("total")
            if status != last_status:
                last_status = status
                last_bucket = -1
                last_progress = -1
                last_progress_at = now
            if isinstance(completed, int) and isinstance(total, int) and total > 0:
                if completed > last_progress:
                    last_progress = completed
                    last_progress_at = now
                elif now - last_progress_at >= stall_timeout:
                    raise TimeoutError(f"Ollama model download made no progress for {stall_timeout:g} seconds")
                bucket = min(20, completed * 20 // total)
                if bucket > last_bucket or completed >= total:
                    print(f"[qwen-init] {status}: {bucket * 5}% ({completed}/{total})", flush=True)
                    last_bucket = bucket
            elif status and last_bucket < 0:
                print(f"[qwen-init] {status}", flush=True)
                last_bucket = 0


def probe_model(model: str) -> None:
    if "embedding" in model.lower():
        reply = request_json(
            "/v1/embeddings",
            {"model": model, "input": ["A2A SynapseRAG readiness probe"]},
            timeout=300,
        )
        rows = reply.get("data") or []
        if not rows or not isinstance(rows[0].get("embedding"), list) or not rows[0]["embedding"]:
            raise RuntimeError(f"Embedding probe for {model} returned no vector")
        return
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Return exactly one JSON object: {\"ok\": true}."}],
        "temperature": 0,
        "max_tokens": 512,
        "stream": False,
        "response_format": {"type": "json_object"},
        "reasoning_effort": "none",
    }
    reply = request_json("/v1/chat/completions", payload, timeout=300)
    response_model = reply.get("model", "")
    if response_model != model:
        raise RuntimeError(f"Ollama returned model {response_model!r}, expected {model!r}")
    content = reply["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    if not isinstance(parsed, dict) or parsed.get("ok") is not True:
        raise RuntimeError(f"Qwen JSON probe returned unexpected content: {content[:200]!r}")


def main() -> int:
    wait_for_server()
    if not MODELS:
        raise RuntimeError("OLLAMA_MODELS did not contain a model name")
    timeout_seconds = max(60, float(os.environ.get("QWEN_DOWNLOAD_TIMEOUT_SECONDS", "3600")))
    attempts = max(1, int(os.environ.get("QWEN_PULL_ATTEMPTS", "3")))
    deadline = time.monotonic() + timeout_seconds
    for model in MODELS:
        if model_present(model):
            print(f"[qwen-init] Reusing cached {model}", flush=True)
        else:
            if os.environ.get("OLLAMA_PULL_MISSING", "true").lower() not in {"true", "1", "yes"}:
                raise RuntimeError(f"Required model {model} is missing from Ollama")
            print(f"[qwen-init] Downloading {model}", flush=True)
            for attempt in range(1, attempts + 1):
                try:
                    pull_model(model, deadline=deadline)
                    if not model_present(model):
                        raise RuntimeError(f"Ollama did not list {model} after pull attempt {attempt}")
                    break
                except (HTTPError, URLError, TimeoutError, ValueError, RuntimeError) as exc:
                    try:
                        if model_present(model):
                            break
                    except (HTTPError, URLError, TimeoutError, ValueError):
                        pass
                    if attempt == attempts or time.monotonic() >= deadline:
                        raise
                    print(f"[qwen-init] Pull attempt {attempt}/{attempts} failed: {exc}; retrying", flush=True)
                    time.sleep(min(5 * attempt, 20))
        if not model_present(model):
            raise RuntimeError(f"Ollama did not list {model} after pull")
        probe_model(model)
        print(f"[qwen-init] {model} is ready for inference", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[qwen-init] ERROR: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from exc
