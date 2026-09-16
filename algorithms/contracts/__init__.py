"""方案 A：算法契约层 — 每个算法只认自己的 I/O，不认整条流水线。"""

from __future__ import annotations

from typing import Any

from algorithms.contracts.normalize import normalize_run_output
from algorithms.contracts.specs import (
    CONTRACTS,
    AlgorithmContract,
    contract_summary,
    get_contract,
)
from algorithms.contracts.validate import (
    ContractError,
    apply_input_aliases,
    extract_list,
    project_inputs,
    validate_inputs,
    validate_outputs,
)


def invoke(
    algorithm_id: str,
    inputs: dict[str, Any],
    *,
    use_mock: bool = True,
    config: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    validate: bool = True,
    tier: str | None = None,
) -> dict[str, Any]:
    """带契约校验的独立调用（推荐外部单独使用算法时走此入口）。

    ``tier`` / ``params.param_tier`` / ``TIA_COMPUTE_PROFILE`` 会合并
    ``config/profiles/{small,medium,large}.yaml``（今早高中低三档）。
    """
    payload = validate_inputs(algorithm_id, dict(inputs or {}), strict=validate)
    contract = get_contract(algorithm_id)
    payload = project_inputs(contract, payload)

    from algorithms import get_predict
    from algorithms.compute_tiers import apply_tier

    merged_cfg = dict(config or {})
    if params:
        merged_cfg.update(params)
    # params/config 里的 tier|param_tier|compute_profile 优先，其次显式 tier 参数
    tier_hint = tier or merged_cfg.get("tier") or merged_cfg.get("param_tier") or merged_cfg.get(
        "compute_profile"
    )
    merged_cfg = apply_tier(merged_cfg, tier=str(tier_hint) if tier_hint else None)

    outputs = get_predict(algorithm_id)(
        payload, use_mock=use_mock, config=merged_cfg, params=params
    )
    if not isinstance(outputs, dict):
        outputs = normalize_run_output(algorithm_id, outputs)
    return validate_outputs(algorithm_id, outputs, strict=validate)


__all__ = [
    "CONTRACTS",
    "AlgorithmContract",
    "ContractError",
    "apply_input_aliases",
    "contract_summary",
    "extract_list",
    "get_contract",
    "invoke",
    "normalize_run_output",
    "project_inputs",
    "validate_inputs",
    "validate_outputs",
]
