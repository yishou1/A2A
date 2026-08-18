"""Local OpenAI-compatible chat server for Qwen3 planning tests."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import threading
from pathlib import Path
from typing import Any

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from transformers import AutoModelForCausalLM, AutoTokenizer


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str = "qwen3:1.7b"
    messages: list[ChatMessage]
    temperature: float = 0.1
    max_tokens: int | None = Field(default=512, alias="max_tokens")
    response_format: dict[str, Any] | None = None
    reasoning_effort: str | None = None


def _resolve_device() -> str:
    requested = os.getenv("LOCAL_QWEN_DEVICE", "auto").strip().lower()
    if requested in {"cuda", "gpu"}:
        if not torch.cuda.is_available():
            raise RuntimeError("LOCAL_QWEN_DEVICE=cuda was requested, but torch.cuda.is_available() is false.")
        return "cuda"
    if requested == "cpu":
        return "cpu"
    if requested not in {"", "auto"}:
        raise RuntimeError(f"Unsupported LOCAL_QWEN_DEVICE={requested!r}; use auto, cuda, or cpu.")
    return "cuda" if torch.cuda.is_available() else "cpu"


def _resolve_dtype(device: str) -> torch.dtype | str:
    requested = os.getenv("LOCAL_QWEN_DTYPE", "auto").strip().lower()
    if requested in {"", "auto"}:
        if device == "cuda":
            return torch.float16
        return "auto"
    if requested in {"fp16", "float16"}:
        return torch.float16
    if requested in {"bf16", "bfloat16"}:
        return torch.bfloat16
    if requested in {"fp32", "float32"}:
        return torch.float32
    raise RuntimeError(f"Unsupported LOCAL_QWEN_DTYPE={requested!r}; use auto, float16, bfloat16, or float32.")


def create_app(model_dir: Path, model_name: str) -> FastAPI:
    app = FastAPI(title="Local Qwen OpenAI-compatible server")
    state: dict[str, Any] = {"ready": False}
    lock = threading.Lock()

    @app.on_event("startup")
    def load_model() -> None:
        device = _resolve_device()
        dtype = _resolve_dtype(device)
        tokenizer = AutoTokenizer.from_pretrained(model_dir, trust_remote_code=True)
        model = AutoModelForCausalLM.from_pretrained(
            model_dir,
            torch_dtype=dtype,
            device_map="auto" if device == "cuda" else None,
            trust_remote_code=True,
            low_cpu_mem_usage=True,
        )
        if device == "cpu":
            model.to("cpu")
        model.eval()
        actual_device = str(next(model.parameters()).device)
        state.update(tokenizer=tokenizer, model=model, ready=True, device=actual_device, dtype=str(dtype))

    @app.get("/health")
    def health() -> dict[str, Any]:
        cuda = torch.cuda.is_available()
        return {
            "status": "ready" if state["ready"] else "loading",
            "model": model_name,
            "device": state.get("device", "unknown"),
            "dtype": state.get("dtype", "unknown"),
            "cuda_available": cuda,
            "cuda_device": torch.cuda.get_device_name(0) if cuda else "",
        }

    @app.get("/api/tags")
    def tags() -> dict[str, Any]:
        return {"models": [{"name": model_name, "model": model_name}]}

    @app.post("/v1/chat/completions")
    def chat_completions(request: ChatRequest) -> dict[str, Any]:
        if not state["ready"]:
            raise HTTPException(status_code=503, detail="model is still loading")
        tokenizer = state["tokenizer"]
        model = state["model"]
        messages = [{"role": item.role, "content": item.content} for item in request.messages]
        wants_json = bool(request.response_format and request.response_format.get("type") == "json_object")
        json_retries = max(0, int(os.getenv("LOCAL_QWEN_JSON_RETRIES", "2"))) if wants_json else 0
        with lock:
            last_error: HTTPException | None = None
            for attempt in range(json_retries + 1):
                attempt_messages = list(messages)
                if wants_json and attempt:
                    attempt_messages.append(
                        {
                            "role": "user",
                            "content": (
                                "上一次输出不是合法 JSON。现在必须只输出一个 JSON object，"
                                "不要 Markdown、不要解释、不要代码块。"
                            ),
                        }
                    )
                prompt = _apply_chat_template(tokenizer, attempt_messages)
                inputs = tokenizer(prompt, return_tensors="pt")
                device = next(model.parameters()).device
                inputs = {key: value.to(device) for key, value in inputs.items()}
                max_new_tokens = max(16, min(int(request.max_tokens or 512), 1024))
                started = time.perf_counter()
                generate_kwargs: dict[str, Any] = {
                    **inputs,
                    "max_new_tokens": max_new_tokens,
                    "do_sample": request.temperature > 0,
                    "pad_token_id": tokenizer.eos_token_id,
                }
                if request.temperature > 0:
                    generate_kwargs["temperature"] = float(request.temperature)
                with torch.inference_mode():
                    output_ids = model.generate(**generate_kwargs)
                new_tokens = output_ids[0, inputs["input_ids"].shape[-1] :]
                content = tokenizer.decode(new_tokens, skip_special_tokens=True).strip()
                duration = max(time.perf_counter() - started, 1e-9)
                if wants_json:
                    try:
                        content = _json_object_or_raise(content)
                        break
                    except HTTPException as exc:
                        last_error = exc
                        if attempt >= json_retries:
                            raise
                        continue
                break
            else:
                if last_error is not None:
                    raise last_error
        return {
            "id": "local-qwen-chatcmpl",
            "object": "chat.completion",
            "model": request.model or model_name,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": int(inputs["input_ids"].shape[-1]),
                "completion_tokens": int(new_tokens.shape[-1]),
                "tokens_per_second": round(float(new_tokens.shape[-1]) / duration, 3),
            },
        }

    return app


def _apply_chat_template(tokenizer: Any, messages: list[dict[str, str]]) -> str:
    try:
        return tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
    except TypeError:
        return tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )


def _json_object_or_raise(content: str) -> str:
    text = _strip_wrappers(content)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"model did not return JSON: {content[:500]}") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=502, detail="model JSON response is not an object")
    return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))


def _strip_wrappers(content: str) -> str:
    text = re.sub(r"<think\b[^>]*>.*?</think>", "", content, flags=re.IGNORECASE | re.DOTALL).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    return text


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11434)
    parser.add_argument("--model-dir", default="local_models/qwen3-1.7b")
    parser.add_argument("--model-name", default="qwen3:1.7b")
    args = parser.parse_args()

    import uvicorn

    app = create_app(Path(args.model_dir).resolve(), args.model_name)
    uvicorn.run(app, host=args.host, port=args.port, log_level=os.getenv("LOG_LEVEL", "info"))


if __name__ == "__main__":
    main()
