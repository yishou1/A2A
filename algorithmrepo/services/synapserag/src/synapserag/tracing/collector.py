"""Request-local collector for deterministic retrieval explanations."""

from __future__ import annotations

import math
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_value(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return [_json_value(item) for item in value.tolist()]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


class RetrievalTraceCollector:
    """Collect one retrieval request without mutating the shared RAG runtime."""

    def __init__(
        self,
        *,
        request_id: str,
        purpose: str,
        query_ids: Sequence[str],
        context: Optional[Dict[str, Any]] = None,
        level: str = "summary",
        index_id: str = "",
        index_version: str = "",
        max_channel_results: int = 20,
        max_ppr_nodes: int = 30,
        max_overlay_nodes: int = 25,
        max_overlay_edges: int = 20,
    ) -> None:
        self.trace_id = f"trace-{uuid.uuid4().hex}"
        self.request_id = request_id
        self.purpose = purpose
        self.query_ids = list(query_ids)
        self.context = dict(context or {})
        self.level = level
        self.index_id = index_id
        self.index_version = index_version
        self.max_channel_results = max(1, max_channel_results)
        self.max_ppr_nodes = max(1, max_ppr_nodes)
        if level == "summary":
            self.max_channel_results = min(self.max_channel_results, 5)
            self.max_ppr_nodes = min(self.max_ppr_nodes, 10)
        self.max_overlay_nodes = max(1, max_overlay_nodes)
        self.max_overlay_edges = max(0, max_overlay_edges)
        self.started_at = _now()
        self._started_perf = time.perf_counter()
        self.completed_at: Optional[str] = None
        self.duration_ms: Optional[float] = None
        self.status = "running"
        self.error: Optional[Dict[str, str]] = None
        self.warnings: List[str] = []
        self.stage_timings_ms: Dict[str, float] = {}
        self.queries: List[Dict[str, Any]] = []

    def begin_query(self, position: int, text: str) -> Dict[str, Any]:
        query_id = self.query_ids[position] if position < len(self.query_ids) else f"query-{position + 1}"
        trace = {
            "query_trace_id": f"qtrace-{uuid.uuid4().hex}",
            "query_id": query_id,
            "text": text,
            "position": position,
            "status": "running",
            "fact_candidates": [],
            "selected_facts": [],
            "seed_nodes": [],
            "ppr_nodes": [],
            "channel_results": {},
            "fusion_results": [],
            "evidence": [],
            "evidence_paths": [],
            "stage_summary": [],
            "display_stats": {},
            "graph_overlay": {"nodes": [], "edges": []},
            "candidate_overlay": {"nodes": [], "edges": []},
            "stage_timings_ms": {},
            "fallback_events": [],
            "warnings": [],
        }
        self.queries.append(trace)
        return trace

    def record_timing(self, trace: Dict[str, Any], stage: str, duration_seconds: float) -> None:
        trace["stage_timings_ms"][stage] = round(max(0.0, duration_seconds) * 1000.0, 3)

    def record_request_timing(self, stage: str, duration_seconds: float) -> None:
        self.stage_timings_ms[stage] = round(max(0.0, duration_seconds) * 1000.0, 3)

    def record_facts(
        self,
        trace: Dict[str, Any],
        *,
        candidates: Iterable[Sequence[Any]],
        selected: Iterable[Sequence[Any]],
        candidate_scores: Optional[Iterable[float]] = None,
        rerank_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        score_values = [] if candidate_scores is None else list(candidate_scores)
        trace["fact_candidates"] = [
            {
                "triple": [str(item) for item in fact],
                "score": float(score_values[index]) if index < len(score_values) else None,
                "rank": index + 1,
            }
            for index, fact in enumerate(list(candidates)[: self.max_channel_results])
        ]
        trace["selected_facts"] = [
            {"triple": [str(item) for item in fact], "rank": index + 1}
            for index, fact in enumerate(list(selected)[: self.max_channel_results])
        ]
        metadata = dict(rerank_metadata or {})
        fallback = metadata.get("fallback")
        if fallback:
            trace["fallback_events"].append({
                "stage": "fact_rerank",
                "fallback": str(fallback),
                "reason": str(metadata.get("fallback_reason") or metadata.get("error") or "unspecified"),
            })
        trace["fact_rerank"] = _json_value({
            key: value
            for key, value in metadata.items()
            if key not in {"facts_before_rerank", "facts_after_rerank"}
        })

    def record_seed_nodes(self, trace: Dict[str, Any], nodes: Iterable[Dict[str, Any]]) -> None:
        ordered = sorted(nodes, key=lambda item: float(item.get("reset_score") or 0.0), reverse=True)
        trace["seed_nodes"] = _json_value(ordered[: self.max_ppr_nodes])

    def record_ppr_nodes(self, trace: Dict[str, Any], nodes: Iterable[Dict[str, Any]]) -> None:
        ordered = sorted(nodes, key=lambda item: float(item.get("ppr_score") or 0.0), reverse=True)
        trace["ppr_nodes"] = _json_value(ordered[: self.max_ppr_nodes])

    def record_channel(
        self,
        trace: Dict[str, Any],
        channel: str,
        document_ids: Iterable[int],
        scores: Iterable[float],
        passage_keys: Sequence[str],
    ) -> None:
        results = []
        for rank, (document_id, score) in enumerate(zip(document_ids, scores), start=1):
            document_id = int(document_id)
            if document_id < 0 or document_id >= len(passage_keys):
                continue
            results.append({
                "rank": rank,
                "document_index": document_id,
                "node_key": str(passage_keys[document_id]),
                "score": float(score),
            })
            if len(results) >= self.max_channel_results:
                break
        trace["channel_results"][channel] = results

    def record_fusion(
        self,
        trace: Dict[str, Any],
        document_ids: Iterable[int],
        scores: Iterable[float],
        contributions: Dict[int, Dict[str, float]],
        passage_keys: Sequence[str],
    ) -> None:
        results = []
        for rank, (document_id, score) in enumerate(zip(document_ids, scores), start=1):
            document_id = int(document_id)
            if document_id < 0 or document_id >= len(passage_keys):
                continue
            results.append({
                "rank": rank,
                "document_index": document_id,
                "node_key": str(passage_keys[document_id]),
                "final_score": float(score),
                "contributions": _json_value(contributions.get(document_id, {})),
            })
            if len(results) >= self.max_channel_results:
                break
        trace["fusion_results"] = results

    def record_evidence(self, position: int, evidence: List[Dict[str, Any]]) -> None:
        if position >= len(self.queries):
            return
        trace = self.queries[position]
        fusion_by_node = {
            item.get("node_key"): item
            for item in trace.get("fusion_results", [])
            if item.get("node_key")
        }
        channel_by_node: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for channel, results in trace.get("channel_results", {}).items():
            for item in results:
                node_key = item.get("node_key")
                if node_key:
                    channel_by_node.setdefault(node_key, {})[channel] = {
                        "rank": item.get("rank"),
                        "score": item.get("score"),
                    }
        enriched = []
        for item in evidence:
            evidence_item = dict(item)
            node_key = evidence_item.get("node_key") or evidence_item.get("chunk_id")
            fusion = fusion_by_node.get(node_key, {})
            channels = channel_by_node.get(node_key, {})
            contributions = fusion.get("contributions", {})
            matched_channels = sorted(set(channels) | set(contributions))
            evidence_item["retrieval_scores"] = {
                "final_score": fusion.get("final_score", evidence_item.get("score")),
                "contributions": contributions,
                "channels": channels,
            }
            evidence_item["retrieval_reason"] = {
                "matched_channels": matched_channels,
                "graph_recalled": "graph" in matched_channels,
                "dense_recalled": "dense" in matched_channels or "dense_fallback" in matched_channels,
                "lexical_recalled": "lexical" in matched_channels,
            }
            enriched.append(evidence_item)
        trace["evidence"] = _json_value(enriched)

    def add_fallback(self, trace: Dict[str, Any], stage: str, fallback: str, reason: str) -> None:
        trace["fallback_events"].append({
            "stage": stage,
            "fallback": fallback,
            "reason": reason,
        })

    def finish_query(self, trace: Dict[str, Any]) -> None:
        trace["status"] = "success"
        self._refresh_stage_summary(trace)

    def fail_query(self, trace: Dict[str, Any], error: Exception) -> None:
        trace["status"] = "failed"
        trace["warnings"].append(str(error))
        self._refresh_stage_summary(trace)

    @staticmethod
    def _refresh_stage_summary(trace: Dict[str, Any]) -> None:
        timings = trace.get("stage_timings_ms", {})
        channels = trace.get("channel_results", {})
        stages = [
            ("fact_candidates", "事实候选", len(trace.get("fact_candidates", [])), "fact_rerank"),
            ("selected_facts", "事实选择", len(trace.get("selected_facts", [])), "fact_rerank"),
            ("ppr", "PPR 图搜索", len(trace.get("ppr_nodes", [])), "ppr"),
            ("graph", "图谱召回", len(channels.get("graph", [])), "graph_retrieval"),
            ("dense", "向量召回", len(channels.get("dense", [])), "dense_retrieval"),
            ("lexical", "关键词召回", len(channels.get("lexical", [])), "lexical_retrieval"),
            ("fusion", "分数融合", len(trace.get("fusion_results", [])), "score_fusion"),
            ("evidence", "最终证据", len(trace.get("evidence", [])), None),
        ]
        trace["stage_summary"] = [
            {
                "stage": stage,
                "label": label,
                "count": count,
                "duration_ms": timings.get(timing_key) if timing_key else None,
            }
            for stage, label, count, timing_key in stages
        ]

    def complete(self) -> None:
        self.status = "success"
        self._finish()

    def fail(self, error_code: str, message: str) -> None:
        self.status = "failed"
        self.error = {"error_code": error_code, "message": message}
        self._finish()

    def _finish(self) -> None:
        self.completed_at = _now()
        self.duration_ms = round((time.perf_counter() - self._started_perf) * 1000.0, 3)

    def to_dict(self) -> Dict[str, Any]:
        for query in self.queries:
            self._refresh_stage_summary(query)
        return _json_value({
            "trace_schema_version": "1.1",
            "trace_id": self.trace_id,
            "request_id": self.request_id,
            "workflow_id": self.context.get("workflow_id"),
            "task_id": self.context.get("task_id"),
            "work_item_id": self.context.get("work_item_id"),
            "agent_id": self.context.get("agent_id"),
            "purpose": self.purpose,
            "index_id": self.index_id,
            "index_version": self.index_version,
            "explain_level": self.level,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
            "stage_timings_ms": self.stage_timings_ms,
            "query_count": len(self.queries),
            "warnings": self.warnings,
            "error": self.error,
            "queries": self.queries,
        })
