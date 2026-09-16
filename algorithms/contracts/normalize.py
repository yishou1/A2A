"""将 backend.run() 的异构返回值归一为契约 dict。"""

from __future__ import annotations

from typing import Any

from algorithms.contracts.specs import get_contract


def normalize_run_output(algorithm_id: str, raw: Any) -> dict[str, Any]:
    """本地 backend.run / RemoteBackend.run 结果 -> 契约形 dict。

    - 已含契约 output_keys 的 dict：原样返回
    - list：写入 run_unwrap_key
    - 无契约键的 dict（如 ImageBind 直接返回 embedding map）：包进 run_unwrap_key
    """
    contract = get_contract(algorithm_id)
    unwrap = contract.run_unwrap_key

    if isinstance(raw, dict):
        if any(k in raw for k in contract.output_keys):
            out = dict(raw)
            if unwrap and unwrap in out and "count" not in out:
                val = out[unwrap]
                if isinstance(val, (list, dict)):
                    out["count"] = len(val)
            return out
        if unwrap is not None:
            return {unwrap: raw, "count": len(raw)}
        return dict(raw)

    if unwrap is None:
        if raw is None:
            return {}
        raise TypeError(
            f"{algorithm_id}: expected dict from run(), got {type(raw).__name__}"
        )

    if isinstance(raw, list):
        return {unwrap: raw, "count": len(raw)}
    return {unwrap: raw}
