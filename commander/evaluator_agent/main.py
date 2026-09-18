from __future__ import annotations

import os

from a2a_protocol.server import A2ABaseAgent
from a2a_sdk import AgentRuntimeSDK
from services.a2a_algorithms_common.rule_based_execution import (
    evaluate_simulated_effect,
)


def evaluate_strike(payload: dict) -> tuple[dict, str]:
    result = evaluate_simulated_effect(payload)
    return result, (
        f"Simulation effect evaluation {result['assessment_status']} "
        f"score={result['eval_score']}"
    )


class EvaluatorAgent(A2ABaseAgent):
    def __init__(self, port: int, **kwargs):
        super().__init__(
            name="Rule_Based_Effect_Evaluator_Agent",
            description="Evaluates tagged simulation evidence without presenting it as real damage.",
            role="evaluator",
            port=port,
            **kwargs,
        )

    def execute_task(self, payload: dict):
        result, message = evaluate_strike(payload)
        output_hint = payload.get("output_hint") or "eval_score"
        return {
            output_hint: result["eval_score"],
            "structured_evaluation_result": result,
        }, message


def main() -> None:
    port = int(os.environ.get("EVALUATOR_AGENT_PORT", "8015"))
    agent = EvaluatorAgent(port=port)
    runtime = AgentRuntimeSDK.from_agent(
        agent,
        heartbeat_interval=float(os.environ.get("A2A_HEARTBEAT_INTERVAL", "5")),
        extra_metadata={
            "capability": "rule_based_effect_evaluation",
            "evaluation_mode": "simulation_only",
        },
    )
    try:
        runtime.serve()
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
