"""Evaluation helpers for compliance and authorization checks."""

from __future__ import annotations

import json
import time

from pathlib import Path
from typing import Any

from decision_agents.agents import ComplianceAuthorizationAgent


def evaluate_compliance_jsonl(path: str | Path) -> dict[str, Any]:
    """Evaluate compliance decisions from JSONL test cases."""
    cases = _load_cases(path)
    agent = ComplianceAuthorizationAgent()
    rows = []
    decision_hits = 0
    predicted_rule_total = 0
    expected_rule_total = 0
    matched_rule_total = 0
    latencies = []

    for case in cases:
        request_payload = dict(case["request"])
        request_payload.setdefault("agent_profile", {})
        request_payload["agent_profile"]["compute_budget"] = "medium"
        started = time.perf_counter()
        response = agent.handle_query(json.dumps(request_payload, ensure_ascii=False))
        latency_ms = (time.perf_counter() - started) * 1000.0
        latencies.append(latency_ms)

        result = response.result
        expected_decision = case["expected_decision"]
        expected_rules = set(case.get("expected_rule_ids", []))
        predicted_rules = _rule_ids(result)
        if result.get("decision") == expected_decision:
            decision_hits += 1
        predicted_rule_total += len(predicted_rules)
        expected_rule_total += len(expected_rules)
        matched_rule_total += len(predicted_rules & expected_rules)
        rows.append(
            {
                "case_id": case.get("case_id"),
                "status": response.status,
                "expected_decision": expected_decision,
                "predicted_decision": result.get("decision"),
                "expected_rule_ids": sorted(expected_rules),
                "predicted_rule_ids": sorted(predicted_rules),
                "latency_ms": round(latency_ms, 3),
            }
        )

    precision = matched_rule_total / predicted_rule_total if predicted_rule_total else 1.0
    recall = matched_rule_total / expected_rule_total if expected_rule_total else 1.0
    f1 = 0.0 if precision + recall == 0.0 else 2 * precision * recall / (precision + recall)
    return {
        "sample_count": len(cases),
        "decision_accuracy": round(decision_hits / len(cases), 4) if cases else 0.0,
        "rule_precision": round(precision, 4),
        "rule_recall": round(recall, 4),
        "rule_f1": round(f1, 4),
        "latency_p50_ms": round(_percentile(latencies, 50), 3),
        "latency_p95_ms": round(_percentile(latencies, 95), 3),
        "latency_max_ms": round(max(latencies), 3) if latencies else 0.0,
        "passed_latency_2s": all(latency <= 2000.0 for latency in latencies),
        "cases": rows,
    }


def _load_cases(path: str | Path) -> list[dict[str, Any]]:
    cases = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        cases.append(json.loads(line))
    return cases


def _rule_ids(result: dict[str, Any]) -> set[str]:
    rule_ids = set()
    for violation in result.get("violations", []):
        rule_ids.add(violation["rule_id"])
    for plan in result.get("per_plan_results", []):
        for violation in plan.get("violations", []):
            rule_ids.add(violation["rule_id"])
    return rule_ids


def _percentile(values: list[float], percentile: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile / 100.0
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = index - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction
