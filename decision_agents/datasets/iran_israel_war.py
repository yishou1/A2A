"""Adapters for Iran-Israel-War-2026 threat-ranking evaluation data."""

from __future__ import annotations

import csv
import math

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


EXPLICIT_LABEL_FIELDS = ("relevance", "severity", "threat_level", "priority")
TARGET_CITY_FIELDS = (
    "targeted_tel_aviv",
    "targeted_jerusalem",
    "targeted_haifa",
    "targeted_negev_beersheba",
)
WEAPON_BOOLEAN_FIELDS = (
    "drones_used",
    "ballistic_missiles_used",
    "cruise_missiles_used",
    "bm_hypersonic",
    "fattah_used",
    "cluster_munitions",
    "cluster_warhead_confirmed",
    "us_bases_targeted",
)


@dataclass(frozen=True)
class ThreatRankingRecord:
    item_id: str
    predicted_score: float
    relevance: float
    label_source: str
    features: dict[str, Any] = field(default_factory=dict)


def load_iran_israel_records(
    path: str | Path,
    *,
    limit: int | None = None,
) -> list[ThreatRankingRecord]:
    """Load local waves/incidents CSV rows into ranking evaluation records."""
    records = []
    with Path(path).open(newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        for row_index, row in enumerate(reader, start=1):
            record = _record_from_row(row, row_index)
            records.append(record)
            if limit is not None and len(records) >= limit:
                break
    return records


def evaluate_threat_ranking(
    records: list[ThreatRankingRecord],
    *,
    k: int = 10,
) -> dict[str, Any]:
    """Evaluate predicted ordering against relevance labels with NDCG/MAP."""
    ordered_predicted = sorted(records, key=lambda item: (-item.predicted_score, item.item_id))
    ordered_expected = sorted(records, key=lambda item: (-item.relevance, item.item_id))
    effective_k = max(1, min(k, len(records))) if records else max(1, k)
    label_sources = sorted({record.label_source for record in records})

    return {
        "sample_count": len(records),
        "label_source": label_sources[0] if len(label_sources) == 1 else "mixed",
        "ndcg@5": round(_ndcg(ordered_predicted, 5), 4),
        f"ndcg@{k}": round(_ndcg(ordered_predicted, effective_k), 4),
        f"map@{k}": round(_mean_average_precision(ordered_predicted, effective_k), 4),
        "predicted_top_ids": [record.item_id for record in ordered_predicted[:effective_k]],
        "expected_top_ids": [record.item_id for record in ordered_expected[:effective_k]],
        "score_breakdown": [
            {
                "item_id": record.item_id,
                "predicted_score": round(record.predicted_score, 3),
                "relevance": record.relevance,
                "label_source": record.label_source,
                "features": record.features,
            }
            for record in ordered_predicted
        ],
    }


def _record_from_row(row: dict[str, str], row_index: int) -> ThreatRankingRecord:
    normalized = {_normalize_key(key): value for key, value in row.items()}
    item_id = _first_text(normalized, ("waveuid", "incidentid", "id")) or f"row-{row_index}"
    explicit_label = _explicit_relevance(normalized)
    if explicit_label is None:
        relevance = _derived_relevance(normalized)
        label_source = "derived_proxy"
    else:
        relevance = explicit_label
        label_source = "explicit"
    predicted_score = _predicted_score(normalized)
    features = {
        "operation": _first_text(normalized, ("operation",)),
        "payload": _first_text(normalized, ("payload",)),
        "targets": _first_text(normalized, ("targets",)),
        "ballistic_missiles_used": _bool_value(normalized.get("ballisticmissilesused")),
        "cluster_munitions": _bool_value(normalized.get("clustermunitions"))
        or _bool_value(normalized.get("clusterwarheadconfirmed")),
        "us_bases_targeted": _bool_value(normalized.get("usbasestargeted")),
        "major_city_targeted": any(
            _bool_value(normalized.get(_normalize_key(field))) for field in TARGET_CITY_FIELDS
        ),
    }
    return ThreatRankingRecord(
        item_id=item_id,
        predicted_score=predicted_score,
        relevance=relevance,
        label_source=label_source,
        features=features,
    )


def _explicit_relevance(normalized: dict[str, str]) -> float | None:
    for field in EXPLICIT_LABEL_FIELDS:
        raw_value = normalized.get(_normalize_key(field))
        if raw_value is None or not str(raw_value).strip():
            continue
        value = _float_value(raw_value)
        if value is None:
            mapped = _categorical_relevance(raw_value)
            if mapped is not None:
                return mapped
            continue
        if "priority" in field:
            return max(0.0, min(4.0, 5.0 - value))
        return max(0.0, min(4.0, value))
    return None


def _derived_relevance(normalized: dict[str, str]) -> float:
    score = 0.0
    if _bool_value(normalized.get("bmhypersonic")) or _bool_value(normalized.get("fattahused")):
        score += 1.2
    if _bool_value(normalized.get("ballisticmissilesused")):
        score += 0.8
    if _bool_value(normalized.get("clustermunitions")) or _bool_value(
        normalized.get("clusterwarheadconfirmed")
    ):
        score += 0.9
    if _bool_value(normalized.get("usbasestargeted")):
        score += 0.7
    if any(_bool_value(normalized.get(_normalize_key(field))) for field in TARGET_CITY_FIELDS):
        score += 0.5
    duration = _float_value(normalized.get("wavedurationminutes"))
    if duration is not None:
        score += min(duration / 120.0, 0.5)
    return round(max(0.0, min(score, 4.0)), 3)


def _predicted_score(normalized: dict[str, str]) -> float:
    score = 0.0
    weights = {
        "bmhypersonic": 28.0,
        "fattahused": 22.0,
        "ballisticmissilesused": 18.0,
        "cruisemissilesused": 12.0,
        "dronesused": 8.0,
        "clustermunitions": 20.0,
        "clusterwarheadconfirmed": 22.0,
        "usbasestargeted": 16.0,
    }
    for field, weight in weights.items():
        if _bool_value(normalized.get(field)):
            score += weight
    if any(_bool_value(normalized.get(_normalize_key(field))) for field in TARGET_CITY_FIELDS):
        score += 10.0
    duration = _float_value(normalized.get("wavedurationminutes"))
    if duration is not None:
        score += min(duration / 120.0, 1.0) * 8.0
    if _bool_value(normalized.get("israeltargeted")):
        score += 6.0
    return round(max(0.0, min(score, 100.0)), 3)


def _ndcg(records: list[ThreatRankingRecord], k: int) -> float:
    if not records:
        return 0.0
    actual = _discounted_gain([record.relevance for record in records[:k]])
    ideal = _discounted_gain(
        sorted((record.relevance for record in records), reverse=True)[:k]
    )
    if ideal <= 0.0:
        return 0.0
    return actual / ideal


def _discounted_gain(relevances: list[float]) -> float:
    total = 0.0
    for index, relevance in enumerate(relevances, start=1):
        total += (2**relevance - 1.0) / math.log2(index + 1)
    return total


def _mean_average_precision(records: list[ThreatRankingRecord], k: int) -> float:
    if not records:
        return 0.0
    threshold = max(1.0, max(record.relevance for record in records) * 0.5)
    relevant_total = sum(1 for record in records if record.relevance >= threshold)
    if relevant_total == 0:
        return 0.0
    hits = 0
    precision_sum = 0.0
    for rank, record in enumerate(records[:k], start=1):
        if record.relevance < threshold:
            continue
        hits += 1
        precision_sum += hits / rank
    return precision_sum / min(relevant_total, k)


def _categorical_relevance(value: str) -> float | None:
    text = value.strip().lower()
    mapping = {
        "critical": 4.0,
        "severe": 4.0,
        "high": 3.0,
        "medium": 2.0,
        "moderate": 2.0,
        "low": 1.0,
        "minimal": 0.0,
        "none": 0.0,
    }
    return mapping.get(text)


def _first_text(normalized: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = normalized.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _float_value(value: str | None) -> float | None:
    if value is None or not str(value).strip():
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _bool_value(value: str | None) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "confirmed"}


def _normalize_key(value: str | None) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())
