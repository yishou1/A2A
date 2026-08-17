import os
import unittest
from unittest.mock import patch

import requests

from decision_agents.common.config import get_settings
from decision_agents.common.schemas import AgentRequest, CandidatePlan, RuleEvidence
from decision_agents.compliance_authorization import local_algorithm as compliance
from decision_agents.decision_planning import local_algorithm as planning
from decision_agents.knowledge.synapserag_client import EvidenceQuery, SynapseRagClient
from decision_agents.knowledge.retrieval import retrieve_rule_rag_result
from decision_agents.rag.pipeline import RagResult


def rag_result(*evidence, status="ok", warnings=None):
    return RagResult(
        evidence=list(evidence),
        answer="evidence summary" if evidence else "",
        model_profile={"enabled": True, "backend": "synapserag", "status": status},
        warnings=list(warnings or []),
        rewritten_query="query",
        keywords=[],
        duration_ms=12.5,
    )


def request_with_plans(*plans, authorization_status="approved"):
    return AgentRequest.model_validate(
        {
            "request_id": "rag-test",
            "candidate_plans": [plan.model_dump(mode="json") for plan in plans],
            "authorization": {
                "status": authorization_status,
                "scope": ["simulation-only decision-support", "law-of-war"],
            },
            "constraints": ["simulation-only decision-support"],
        }
    )


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class SynapseRagClientTests(unittest.TestCase):
    def test_maps_query_id_to_rule_evidence_and_sends_token(self):
        body = {
            "status": "success",
            "results": [
                {
                    "query_id": "RULE-BLOCK-001",
                    "evidence": [
                        {
                            "chunk_id": "chunk-1",
                            "document_id": "roe",
                            "source": "ROE.pdf",
                            "title": "Authorization",
                            "text": "Direct execution requires authorization.",
                            "score": 0.86,
                            "page_start": 12,
                            "page_end": 12,
                            "section": "Authorization",
                            "citation": "ROE.pdf p.12",
                            "content_hash": "abc",
                        }
                    ],
                }
            ],
            "retrieval_profile": {"duration_ms": 25.0, "index_id": "kb-v1"},
            "warnings": [],
        }
        env = {
            **os.environ,
            "RAG_BACKEND": "synapserag",
            "SYNAPSERAG_BASE_URL": "http://rag.local:8000",
            "SYNAPSERAG_API_TOKEN": "secret",
        }
        with patch.dict(os.environ, env, clear=True), patch(
            "decision_agents.knowledge.synapserag_client.requests.post",
            return_value=FakeResponse(body),
        ) as post:
            result = SynapseRagClient(get_settings()).retrieve(
                [EvidenceQuery("RULE-BLOCK-001", "blocked action")],
                request_id="request-1",
                purpose="planning",
                top_k=3,
            )

        self.assertEqual(result.evidence[0].rule_id, "RULE-BLOCK-001")
        self.assertEqual(result.evidence[0].citation, "ROE.pdf p.12")
        self.assertEqual(result.duration_ms, 25.0)
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer secret")

    def test_http_failure_is_returned_as_structured_rag_error(self):
        env = {**os.environ, "RAG_BACKEND": "synapserag"}
        with patch.dict(os.environ, env, clear=True), patch(
            "decision_agents.knowledge.synapserag_client.requests.post",
            side_effect=requests.Timeout("offline"),
        ):
            result = SynapseRagClient(get_settings()).retrieve(
                [EvidenceQuery("RULE-1", "rule")],
                request_id="request-1",
                purpose="planning",
                top_k=3,
            )

        self.assertEqual(result.model_profile["status"], "error")
        self.assertTrue(result.warnings[0].startswith("RAG_RETRIEVAL_ERROR:Timeout"))

    def test_disabled_backend_returns_no_evidence_without_fallback(self):
        env = {**os.environ, "RAG_BACKEND": "disabled"}
        with patch.dict(os.environ, env, clear=True):
            result = retrieve_rule_rag_result(
                [EvidenceQuery("RULE-1", "rule")],
                purpose="compliance",
            )

        self.assertFalse(result.evidence)
        self.assertEqual(result.model_profile["backend"], "disabled")
        self.assertEqual(result.warnings, ["rag_disabled"])

    def test_missing_query_result_is_rejected(self):
        body = {
            "status": "success",
            "results": [],
            "retrieval_profile": {"duration_ms": 1.0},
            "warnings": [],
        }
        with patch(
            "decision_agents.knowledge.synapserag_client.requests.post",
            return_value=FakeResponse(body),
        ):
            result = SynapseRagClient(get_settings()).retrieve(
                [EvidenceQuery("RULE-1", "rule")],
                request_id="request-1",
                purpose="planning",
                top_k=3,
            )

        self.assertEqual(result.model_profile["status"], "error")
        self.assertIn("missing query IDs", result.warnings[0])


class PlanningRagAdjustmentTests(unittest.TestCase):
    def setUp(self):
        self.blocked = CandidatePlan(
            id="PLAN-BLOCKED",
            name="Blocked plan",
            actions=["execute direct action", "simulation-only decision-support"],
            score=80.0,
        )
        self.safe = CandidatePlan(
            id="PLAN-SAFE",
            name="Safe plan",
            actions=["monitor and report", "simulation-only decision-support"],
            score=70.0,
        )
        self.request = request_with_plans(self.blocked, self.safe)

    def test_blocking_rule_adjusts_score_and_reorders_plans(self):
        evidence = RuleEvidence(
            source="ROE.pdf",
            rule_id="RULE-BLOCK-001",
            title="Execution",
            text="Direct execution is blocked.",
            score=0.8,
            citation="ROE.pdf p.1",
        )
        with patch.object(
            planning,
            "retrieve_rule_rag_result",
            return_value=rag_result(evidence),
        ):
            plans, payload = planning.enhance_plans_with_rag(
                [self.blocked, self.safe], self.request
            )

        by_id = {plan.id: plan for plan in plans}
        self.assertEqual(by_id["PLAN-BLOCKED"].rag_rule_adjustment, -19.0)
        self.assertEqual(by_id["PLAN-BLOCKED"].final_score, 61.0)
        self.assertEqual(by_id["PLAN-BLOCKED"].evidence_rule_ids, ["RULE-BLOCK-001"])
        self.assertEqual(plans[0].id, "PLAN-SAFE")
        self.assertEqual(payload["rag_duration_ms"], 12.5)

    def test_retrieval_failure_uses_deterministic_point_seven_five_factor(self):
        failed = rag_result(
            status="error",
            warnings=["RAG_RETRIEVAL_ERROR:timeout"],
        )
        with patch.object(planning, "retrieve_rule_rag_result", return_value=failed):
            plans, payload = planning.enhance_plans_with_rag([self.blocked], self.request)

        self.assertEqual(plans[0].rag_rule_adjustment, -15.0)
        self.assertIn("RAG_RETRIEVAL_ERROR:timeout", payload["rag_warnings"])


class ComplianceRagPolicyTests(unittest.TestCase):
    def test_evidence_is_bound_before_logistic_features_are_built(self):
        plan = CandidatePlan(
            id="PLAN-BLOCKED",
            name="Blocked plan",
            status="recommended",
            actions=["execute direct action", "simulation-only decision-support"],
            score=80.0,
        )
        request = request_with_plans(plan)
        evidence = RuleEvidence(
            source="ROE.pdf",
            rule_id="RULE-BLOCK-001",
            title="Execution",
            text="Direct execution is blocked.",
            score=0.9,
        )
        with patch.object(
            compliance,
            "retrieve_rule_rag_result",
            return_value=rag_result(evidence),
        ):
            result = compliance.evaluate_compliance(request, use_rule_table=True)
            features = compliance._compliance_logistic_features(result, request)

        violation = next(item for item in result.violations if item.rule_id == "RULE-BLOCK-001")
        self.assertEqual(violation.evidence_rule_ids, ["RULE-BLOCK-001"])
        self.assertGreater(features["rag_evidence_count"], 0.0)

    def test_retrieval_failure_forces_review_but_preserves_blocked(self):
        safe = CandidatePlan(
            id="PLAN-SAFE",
            name="Safe plan",
            status="recommended",
            actions=["monitor and report", "simulation-only decision-support"],
            score=80.0,
        )
        failed = rag_result(
            status="error",
            warnings=["RAG_RETRIEVAL_ERROR:timeout"],
        )
        with patch.object(compliance, "retrieve_rule_rag_result", return_value=failed):
            safe_result = compliance.evaluate_compliance(
                request_with_plans(safe), use_rule_table=True
            )
        self.assertEqual(safe_result.decision, "review_required")
        self.assertFalse(safe_result.approved_for_demo_handoff)

        blocked = safe.model_copy(
            update={"id": "PLAN-BLOCKED", "actions": ["execute direct action"]}
        )
        with patch.object(compliance, "retrieve_rule_rag_result", return_value=failed):
            blocked_result = compliance.evaluate_compliance(
                request_with_plans(blocked), use_rule_table=True
            )
        self.assertEqual(blocked_result.decision, "blocked")


if __name__ == "__main__":
    unittest.main()
