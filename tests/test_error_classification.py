from __future__ import annotations

import unittest

import requests

from a2a_protocol.client import A2AClientError
from commander_agent.error_classification import classify_agent_error


class AgentErrorClassificationTest(unittest.TestCase):
    def test_connection_error_is_failover_system_error(self):
        info = classify_agent_error(
            requests.exceptions.ConnectionError("connection refused")
        )

        self.assertEqual(info.code, "AGENT_UNAVAILABLE")
        self.assertEqual(info.category, "system")
        self.assertTrue(info.failover)
        self.assertTrue(info.retryable)

    def test_timeout_is_failover_system_error(self):
        info = classify_agent_error(requests.exceptions.ReadTimeout("read timed out"))

        self.assertEqual(info.code, "AGENT_TIMEOUT")
        self.assertTrue(info.failover)

    def test_agent_not_ready_payload_is_failover_error(self):
        info = classify_agent_error(
            A2AClientError(
                "agent is not ready",
                response_payload={
                    "status": "failed",
                    "error": "agent is not ready",
                    "error_code": "AGENT_NOT_READY",
                },
            )
        )

        self.assertEqual(info.code, "AGENT_NOT_READY")
        self.assertTrue(info.failover)

    def test_business_error_payload_does_not_failover(self):
        info = classify_agent_error(
            A2AClientError(
                "invalid coordinates",
                response_payload={
                    "status": "failed",
                    "error": "invalid coordinates",
                    "error_code": "AGENT_BUSINESS_ERROR",
                },
            )
        )

        self.assertEqual(info.code, "AGENT_BUSINESS_ERROR")
        self.assertEqual(info.category, "business")
        self.assertFalse(info.failover)

    def test_algorithm_input_error_is_non_retryable_business_error(self):
        info = classify_agent_error(
            A2AClientError(
                "missing candidate plans",
                response_payload={
                    "status": "failed",
                    "error": "missing candidate plans",
                    "error_code": "ALGORITHM_INPUT_ERROR",
                },
            )
        )

        self.assertEqual(info.category, "business")
        self.assertFalse(info.failover)
        self.assertFalse(info.retryable)

    def test_runtime_provider_errors_trigger_failover(self):
        expected_categories = {
            "ALGORITHM_RUNTIME_ERROR": "algorithm",
            "LLM_PROVIDER_ERROR": "model",
            "RAG_RETRIEVAL_ERROR": "retrieval",
        }
        for error_code, category in expected_categories.items():
            with self.subTest(error_code=error_code):
                info = classify_agent_error(
                    A2AClientError(
                        "dependency unavailable",
                        response_payload={
                            "status": "failed",
                            "error": "dependency unavailable",
                            "error_code": error_code,
                        },
                    )
                )

                self.assertEqual(info.category, category)
                self.assertTrue(info.failover)
                self.assertTrue(info.retryable)

    def test_contract_errors_remain_non_retryable_protocol_errors(self):
        for error_code in ("SCHEMA_VALIDATION_ERROR", "OUTPUT_CONTRACT_ERROR"):
            with self.subTest(error_code=error_code):
                info = classify_agent_error(
                    A2AClientError(
                        "invalid contract",
                        response_payload={
                            "status": "failed",
                            "error": "invalid contract",
                            "error_code": error_code,
                        },
                    )
                )

                self.assertEqual(info.category, "protocol")
                self.assertFalse(info.failover)
                self.assertFalse(info.retryable)

    def test_http_status_classification_distinguishes_5xx_and_4xx(self):
        unavailable_response = requests.Response()
        unavailable_response.status_code = 503
        unavailable = requests.exceptions.HTTPError(
            "503 service unavailable",
            response=unavailable_response,
        )

        protocol_response = requests.Response()
        protocol_response.status_code = 401
        protocol = requests.exceptions.HTTPError(
            "401 unauthorized",
            response=protocol_response,
        )

        self.assertTrue(classify_agent_error(unavailable).failover)
        self.assertEqual(classify_agent_error(unavailable).code, "AGENT_HTTP_5XX")
        self.assertFalse(classify_agent_error(protocol).failover)
        self.assertEqual(classify_agent_error(protocol).code, "AGENT_PROTOCOL_ERROR")


if __name__ == "__main__":
    unittest.main()
