"""契约校验与输入归一（别名映射）。"""

from __future__ import annotations

from typing import Any

from algorithms.contracts.specs import AlgorithmContract, get_contract


class ContractError(ValueError):
    """输入/输出不符合算法契约。"""


def apply_input_aliases(
    algorithm_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """将外部别名键映射为契约键；不覆盖已存在的契约键。"""
    contract = get_contract(algorithm_id)
    out = dict(payload)
    for alias, canonical in contract.input_aliases.items():
        if alias in out and canonical not in out:
            out[canonical] = out.pop(alias)
        elif alias in out:
            out.pop(alias, None)
    return out


def validate_inputs(
    algorithm_id: str,
    payload: dict[str, Any],
    *,
    strict: bool = True,
) -> dict[str, Any]:
    """校验并返回规范化后的输入 dict。

    strict=True：缺少 required_inputs 则抛 ContractError。
    marl_ppo_task_scheduler 特例：tracks 或 amos_payload 至少一个即可。
    """
    contract = get_contract(algorithm_id)
    normalized = apply_input_aliases(algorithm_id, payload)

    if algorithm_id == "marl_ppo_task_scheduler":
        if strict and not (
            normalized.get("amos_payload")
            or normalized.get("tracks")
            or normalized.get("detections")
        ):
            raise ContractError(
                f"{algorithm_id}: need amos_payload or tracks/detections"
            )
        return normalized

    if algorithm_id == "battlefield_rtdetr_detector":
        if not normalized.get("frames") and normalized.get("media_refs"):
            normalized["frames"] = [
                {
                    "sensor_id": "MEDIA-0",
                    "modality": "eo_ir",
                    "payload": {"media_refs": normalized["media_refs"]},
                }
            ]
        if strict and not normalized.get("frames"):
            raise ContractError(f"{algorithm_id}: missing required input 'frames'")
        return normalized

    if strict:
        missing = [
            k
            for k in contract.required_inputs
            if k not in normalized or normalized.get(k) is None
        ]
        if missing:
            raise ContractError(
                f"{algorithm_id}: missing required inputs: {', '.join(missing)}"
            )
    return normalized


def validate_outputs(
    algorithm_id: str,
    outputs: dict[str, Any],
    *,
    strict: bool = True,
) -> dict[str, Any]:
    """校验输出至少包含契约定义的一个主键。"""
    if not isinstance(outputs, dict):
        raise ContractError(f"{algorithm_id}: output must be a dict, got {type(outputs).__name__}")
    contract = get_contract(algorithm_id)
    if not contract.output_keys:
        return outputs
    present = [k for k in contract.output_keys if k in outputs]
    if strict and not present:
        raise ContractError(
            f"{algorithm_id}: output missing keys (need one of {list(contract.output_keys)})"
        )
    return outputs


def extract_list(value: Any, *keys: str) -> list:
    """从 list 或 dict 中按键提取列表（流水线适配常用）。"""
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in keys:
            item = value.get(key)
            if isinstance(item, list):
                return item
    return []


def project_inputs(contract: AlgorithmContract, payload: dict[str, Any]) -> dict[str, Any]:
    """只保留契约允许的输入键（防泄漏上下游字段）。"""
    allowed = set(contract.required_inputs) | set(contract.optional_inputs)
    return {k: v for k, v in payload.items() if k in allowed}
