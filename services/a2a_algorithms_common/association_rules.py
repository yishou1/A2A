"""Association rule mining and online matching for execution control."""
from __future__ import annotations

import json
from collections import Counter
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Sequence, Set, Tuple


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_training_records(path: Path | None = None) -> List[dict]:
    records_path = path or (_repo_root() / "data" / "execution_control" / "fixtures" / "rule_training_records.json")
    payload = json.loads(records_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("rule training records must be a JSON list")
    return payload


def _transactions(records: Sequence[dict]) -> List[Tuple[frozenset, dict]]:
    rows: List[Tuple[frozenset, dict]] = []
    for record in records:
        items = frozenset(str(item) for item in (record.get("items") or []))
        consequent = dict(record.get("consequent") or {})
        if items and consequent:
            rows.append((items, consequent))
    return rows


def _support(itemset: frozenset, transactions: Sequence[frozenset]) -> float:
    if not transactions:
        return 0.0
    hits = sum(1 for txn in transactions if itemset.issubset(txn))
    return hits / len(transactions)


def mine_association_rules(
    records: Sequence[dict],
    *,
    min_support: float = 0.2,
    min_confidence: float = 0.6,
    max_itemset_size: int = 4,
) -> List[dict]:
    """Mine association rules from historical task records using Apriori-style pruning."""
    rows = _transactions(records)
    transactions = [items for items, _ in rows]
    item_counts: Counter[str] = Counter()
    for txn in transactions:
        item_counts.update(txn)

    frequent: Dict[int, Set[frozenset]] = {1: set()}
    total = max(1, len(transactions))
    for item, count in item_counts.items():
        if count / total >= min_support:
            frequent[1].add(frozenset([item]))

    for size in range(2, max_itemset_size + 1):
        prev = sorted(frequent.get(size - 1, set()), key=lambda s: tuple(sorted(s)))
        candidates: Set[frozenset] = set()
        for left, right in combinations(prev, 2):
            union = left | right
            if len(union) != size:
                continue
            if all(union - frozenset([item]) in frequent[size - 1] for item in union):
                candidates.add(union)
        level: Set[frozenset] = set()
        for candidate in candidates:
            if _support(candidate, transactions) >= min_support:
                level.add(candidate)
        if level:
            frequent[size] = level

    all_itemsets: List[frozenset] = []
    for level in frequent.values():
        all_itemsets.extend(sorted(level, key=lambda s: (-len(s), tuple(sorted(s)))))

    rules: List[dict] = []
    seen: Set[Tuple[frozenset, str, str]] = set()
    for antecedent in all_itemsets:
        matching = [consequent for items, consequent in rows if antecedent.issubset(items)]
        if not matching:
            continue
        support = _support(antecedent, transactions)
        confidence = len(matching) / max(1, sum(1 for txn in transactions if antecedent.issubset(txn)))
        if support < min_support or confidence < min_confidence:
            continue
        vote = Counter(
            (
                str(item.get("action") or ""),
                str(item.get("executor_role") or ""),
                str(item.get("coordination_group") or ""),
            )
            for item in matching
        )
        action, executor_role, coordination_group = vote.most_common(1)[0][0]
        priority_values = [
            float(item.get("priority") or 0.5)
            for item in matching
            if str(item.get("action") or "") == action and str(item.get("executor_role") or "") == executor_role
        ]
        key = (antecedent, action, executor_role)
        if key in seen:
            continue
        seen.add(key)
        rules.append(
            {
                "rule_id": f"RULE-{len(rules) + 1:03d}",
                "antecedent": sorted(antecedent),
                "support": round(support, 4),
                "confidence": round(confidence, 4),
                "consequent": {
                    "action": action,
                    "executor_role": executor_role,
                    "priority": round(sum(priority_values) / max(1, len(priority_values)), 4),
                    "coordination_group": coordination_group or "GROUP-DEFAULT",
                },
            }
        )
    rules.sort(key=lambda item: (-float(item["confidence"]), -float(item["support"]), item["rule_id"]))
    return rules


def save_rules(rules: Sequence[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(list(rules), ensure_ascii=False, indent=2), encoding="utf-8")


def load_or_mine_rules(
    *,
    records_path: Path | None = None,
    rules_path: Path | None = None,
    refresh: bool = False,
) -> List[dict]:
    rules_path = rules_path or (_repo_root() / "data" / "execution_control" / "processed" / "mined_rules.json")
    if rules_path.exists() and not refresh:
        return json.loads(rules_path.read_text(encoding="utf-8"))
    records = load_training_records(records_path)
    rules = mine_association_rules(records)
    save_rules(rules, rules_path)
    return rules


def discretize_situation(situation: dict, phase: str) -> Set[str]:
    threat = float(situation.get("threat_score") or 0.5)
    intel = float(situation.get("intel_confidence") or 0.5)
    readiness = float(situation.get("resource_readiness") or 0.5)
    decision = str(situation.get("commander_decision") or "").upper()

    items = {f"phase={phase}"}
    if threat >= 0.72:
        items.add("threat=high")
    elif threat >= 0.45:
        items.add("threat=medium")
    else:
        items.add("threat=low")

    if intel >= 0.8:
        items.add("intel=good")
    elif intel >= 0.6:
        items.add("intel=fair")
    else:
        items.add("intel=poor")

    if readiness >= 0.75:
        items.add("resource=ready")
    else:
        items.add("resource=limited")

    if phase == "assault":
        if "RE-PLAN" in decision or "ABORT" in decision:
            items.add("decision=replan")
        else:
            items.add("decision=assault")
    return items


def match_rules(current_items: Set[str], rules: Sequence[dict], *, phase: str) -> List[dict]:
    matched: List[dict] = []
    for rule in rules:
        antecedent = set(rule.get("antecedent") or [])
        if not antecedent.issubset(current_items):
            continue
        consequent = dict(rule.get("consequent") or {})
        if phase == "strike" and consequent.get("executor_role") != "artillery":
            continue
        if phase == "assault" and consequent.get("executor_role") != "assault":
            continue
        matched.append(
            {
                "rule_id": rule.get("rule_id"),
                "support": rule.get("support"),
                "confidence": rule.get("confidence"),
                "antecedent": sorted(antecedent),
                "consequent": consequent,
            }
        )
    matched.sort(key=lambda item: (-float(item.get("confidence") or 0.0), item.get("rule_id") or ""))
    return matched


def choose_primary_rule(matched_rules: Sequence[dict], *, default_executor_role: str) -> dict:
    if matched_rules:
        return dict(matched_rules[0])
    return {
        "rule_id": "RULE-DEFAULT",
        "support": 0.0,
        "confidence": 0.0,
        "antecedent": [],
        "consequent": {
            "action": "observe_and_hold" if default_executor_role == "artillery" else "hold_and_recon",
            "executor_role": default_executor_role,
            "priority": 0.5,
            "coordination_group": "GROUP-DEFAULT",
        },
    }
