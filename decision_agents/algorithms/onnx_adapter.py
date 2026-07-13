"""Optional ONNX runtime bridge for algorithm registry entries."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from decision_agents.schemas import AgentRequest


PreprocessFn = Callable[[AgentRequest], dict[str, Any]]
PostprocessFn = Callable[[Any, AgentRequest], dict[str, Any]]
FallbackFn = Callable[[AgentRequest], dict[str, Any]]

_SESSION_CACHE: dict[tuple[str, tuple[str, ...]], Any] = {}


@dataclass(frozen=True)
class OnnxAlgorithmSpec:
    model_path: str
    input_names: tuple[str, ...]
    output_names: tuple[str, ...]
    preprocess_fn: PreprocessFn
    postprocess_fn: PostprocessFn
    providers: tuple[str, ...] = ("CPUExecutionProvider",)
    fallback_algorithm_id: str = ""
    fallback_run_fn: FallbackFn | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def run_onnx_or_fallback(request: AgentRequest, spec: OnnxAlgorithmSpec) -> dict[str, Any]:
    path = Path(spec.model_path).expanduser()
    if not path.exists():
        return _fallback(request, spec, f"model_not_found:{path}")

    try:
        import onnxruntime as ort
    except Exception as exc:
        return _fallback(request, spec, f"onnxruntime_unavailable:{exc}")

    try:
        cache_key = (str(path.resolve()), tuple(spec.providers))
        session = _SESSION_CACHE.get(cache_key)
        if session is None:
            session = ort.InferenceSession(str(path), providers=list(spec.providers))
            _SESSION_CACHE[cache_key] = session
        outputs = session.run(list(spec.output_names) or None, spec.preprocess_fn(request))
        result = spec.postprocess_fn(outputs, request)
        result.setdefault("onnx", {})
        result["onnx"].update(
            {
                "model_path": str(path),
                "providers": list(spec.providers),
                "fallback": False,
                **spec.metadata,
            }
        )
        return result
    except Exception as exc:
        return _fallback(request, spec, f"onnx_inference_failed:{exc}")


def _fallback(request: AgentRequest, spec: OnnxAlgorithmSpec, reason: str) -> dict[str, Any]:
    if spec.fallback_run_fn is None:
        return {
            "method": "onnx_unavailable",
            "onnx": {
                "model_path": spec.model_path,
                "providers": list(spec.providers),
                "fallback": True,
                "fallback_algorithm_id": spec.fallback_algorithm_id,
                "reason": reason,
                **spec.metadata,
            },
        }
    result = spec.fallback_run_fn(request)
    result["onnx"] = {
        "model_path": spec.model_path,
        "providers": list(spec.providers),
        "fallback": True,
        "fallback_algorithm_id": spec.fallback_algorithm_id,
        "reason": reason,
        **spec.metadata,
    }
    return result
