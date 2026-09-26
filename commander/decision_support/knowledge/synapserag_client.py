"""HTTP adapter for SynapseRAG's retrieval-only API."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

import requests

from decision_support.config import Settings
from decision_support.schemas import RuleEvidence
from decision_support.rag.pipeline import RagResult


@dataclass(frozen=True)
class EvidenceQuery:
    query_id: str
    text: str


class SynapseRagClient:
    def __init__(self, settings: Settings) -> None:
        self.base_url = settings.synapserag_base_url
        self.api_token = settings.synapserag_api_token
        self.timeout = settings.synapserag_timeout_seconds
        self.transport = settings.synapserag_transport
        self.algolib_base_url = settings.algolib_base_url

    def retrieve(
        self,
        queries: list[EvidenceQuery],
        *,
        request_id: str,
        purpose: str,
        top_k: int,
        context: dict[str, str] | None = None,
        explain_level: str = "detailed",
    ) -> RagResult:
        headers = {"Content-Type": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        payload = {
            "schema_version": "1.0",
            "request_id": request_id,
            "purpose": purpose,
            "top_k": max(1, min(10, top_k)),
            "context": {key: value for key, value in (context or {}).items() if value},
            "explain": {"enabled": True, "level": explain_level},
            "queries": [
                {"query_id": item.query_id, "text": item.text}
                for item in queries
            ],
        }
        try:
            if self.transport not in {"direct", "algolib"}:
                raise ValueError("SYNAPSERAG_TRANSPORT must be direct or algolib")
            url = f"{self.base_url}/api/retrieve"
            if self.transport == "algolib":
                url = f"{self.algolib_base_url.rstrip('/')}/run"
                payload = {
                    "request_id": request_id,
                    "algorithm_id": "synapse_rag_retriever",
                    "version": "2.0.0",
                    "backend_type": "python_http_service",
                    "inputs": payload,
                    "params": {},
                }
                headers = {"Content-Type": "application/json"}
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
            if self.transport == "algolib":
                if not isinstance(body, dict) or body.get("ok") is not True:
                    raise ValueError(f"algorithm-library retrieval failed: {body}")
                body = body["outputs"]
            return _parse_response(body, queries, purpose)
        except (requests.RequestException, ValueError, TypeError, KeyError) as error:
            return failed_rag_result(
                backend="synapserag",
                warning=f"RAG_RETRIEVAL_ERROR:{type(error).__name__}:{error}",
                rewritten_query="\n".join(item.text for item in queries),
                details={"base_url": self.base_url},
            )


def _parse_response(
    body: dict[str, Any],
    queries: list[EvidenceQuery],
    purpose: str,
) -> RagResult:
    if not isinstance(body, dict):
        raise ValueError("SynapseRAG response must be a JSON object")
    if body.get("status") != "success" or not isinstance(body.get("results"), list):
        raise ValueError("invalid SynapseRAG response envelope")
    requested_ids = {item.query_id for item in queries}
    returned_ids: set[str] = set()
    evidence: list[RuleEvidence] = []
    for result in body["results"]:
        query_id = str(result["query_id"])
        if query_id not in requested_ids:
            raise ValueError(f"unexpected query_id: {query_id}")
        returned_ids.add(query_id)
        for item in result.get("evidence", []):
            text = str(item.get("text") or "")
            if not text:
                continue
            evidence.append(
                RuleEvidence(
                    source=str(item.get("source") or "synapserag"),
                    rule_id=query_id,
                    title=str(item.get("title") or item.get("source") or query_id),
                    text=text,
                    score=max(0.0, float(item.get("score") or 0.0)),
                    tags=["synapserag", purpose],
                    doc_id=str(item.get("document_id") or "") or None,
                    doc_type="synapserag",
                    page_start=item.get("page_start"),
                    page_end=item.get("page_end"),
                    section=item.get("section"),
                    chunk_id=item.get("chunk_id"),
                    citation=item.get("citation"),
                    content_hash=item.get("content_hash") or sha256(text.encode("utf-8")).hexdigest(),
                )
            )
    if returned_ids != requested_ids:
        missing = ",".join(sorted(requested_ids - returned_ids))
        raise ValueError(f"SynapseRAG response is missing query IDs: {missing}")
    profile = dict(body.get("retrieval_profile") or {})
    profile.update({"enabled": True, "backend": "synapserag", "status": "ok"})
    if body.get("trace_id"):
        profile["trace_id"] = body["trace_id"]
    warnings = [str(item) for item in body.get("warnings", [])]
    return RagResult(
        evidence=evidence,
        answer=_deterministic_answer(purpose, evidence),
        model_profile=profile,
        warnings=warnings,
        rewritten_query="\n".join(item.text for item in queries),
        keywords=[],
        duration_ms=float(profile.get("duration_ms") or 0.0),
    )


def failed_rag_result(
    *,
    backend: str,
    warning: str,
    rewritten_query: str,
    details: dict[str, Any] | None = None,
) -> RagResult:
    profile = {"enabled": backend != "disabled", "backend": backend, "status": "error"}
    if details:
        profile.update(details)
    return RagResult(
        evidence=[],
        answer="",
        model_profile=profile,
        warnings=[warning],
        rewritten_query=rewritten_query,
        keywords=[],
        duration_ms=0.0,
    )


def _deterministic_answer(purpose: str, evidence: list[RuleEvidence]) -> str:
    if not evidence:
        return "未检索到可绑定的知识库证据。"
    citations = []
    for item in evidence:
        label = f"{item.rule_id} {item.citation or item.source}"
        if label not in citations:
            citations.append(label)
    subject = "方案" if purpose == "planning" else "合规审查"
    return f"{subject}依据参考：{'；'.join(citations[:5])}。"
