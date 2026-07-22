from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional

import requests


class AgentErrorCode(str, Enum):
    AGENT_UNAVAILABLE = "AGENT_UNAVAILABLE"
    AGENT_TIMEOUT = "AGENT_TIMEOUT"
    AGENT_NOT_READY = "AGENT_NOT_READY"
    AGENT_HEARTBEAT_LOST = "AGENT_HEARTBEAT_LOST"
    AGENT_HTTP_5XX = "AGENT_HTTP_5XX"
    AGENT_RESOURCE_EXHAUSTED = "AGENT_RESOURCE_EXHAUSTED"
    AGENT_PROTOCOL_ERROR = "AGENT_PROTOCOL_ERROR"
    AGENT_BUSINESS_ERROR = "AGENT_BUSINESS_ERROR"
    MODEL_INVOCATION_ERROR = "MODEL_INVOCATION_ERROR"
    AGENT_LATE_RESPONSE = "AGENT_LATE_RESPONSE"
    AGENT_UNKNOWN_ERROR = "AGENT_UNKNOWN_ERROR"


FAILOVER_ERROR_CODES = {
    AgentErrorCode.AGENT_UNAVAILABLE.value,
    AgentErrorCode.AGENT_TIMEOUT.value,
    AgentErrorCode.AGENT_NOT_READY.value,
    AgentErrorCode.AGENT_HEARTBEAT_LOST.value,
    AgentErrorCode.AGENT_HTTP_5XX.value,
    AgentErrorCode.AGENT_RESOURCE_EXHAUSTED.value,
}


@dataclass(frozen=True)
class AgentErrorInfo:
    code: str
    category: str
    message: str
    failover: bool
    retryable: bool

    def trace_fields(self) -> dict:
        return {
            "error_code": self.code,
            "error_category": self.category,
            "failover": self.failover,
            "retryable": self.retryable,
        }


def classify_agent_error(error: Any) -> AgentErrorInfo:
    message = "" if error is None else str(error)

    payload_info = _classify_response_payload(error, message)
    if payload_info is not None:
        return payload_info

    if isinstance(error, requests.exceptions.Timeout):
        return _info(AgentErrorCode.AGENT_TIMEOUT, message, failover=True, retryable=True)

    if isinstance(error, requests.exceptions.ConnectionError):
        return _info(AgentErrorCode.AGENT_UNAVAILABLE, message, failover=True, retryable=True)

    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code is not None:
        if int(status_code) >= 500:
            return _info(AgentErrorCode.AGENT_HTTP_5XX, message, failover=True, retryable=True)
        return _info(AgentErrorCode.AGENT_PROTOCOL_ERROR, message, failover=False, retryable=False)

    text = message.lower()
    if any(marker in text for marker in ("connection refused", "connection aborted", "connection reset")):
        return _info(AgentErrorCode.AGENT_UNAVAILABLE, message, failover=True, retryable=True)
    if "failed to establish a new connection" in text or "max retries exceeded" in text:
        return _info(AgentErrorCode.AGENT_UNAVAILABLE, message, failover=True, retryable=True)
    if any(marker in text for marker in ("read timed out", "connect timeout", "timed out")):
        return _info(AgentErrorCode.AGENT_TIMEOUT, message, failover=True, retryable=True)
    if "heartbeat lost" in text:
        return _info(AgentErrorCode.AGENT_HEARTBEAT_LOST, message, failover=True, retryable=True)
    if any(
        marker in text
        for marker in (
            "resource exhausted",
            "resource insufficient",
            "insufficient resource",
            "out of memory",
            "资源不足",
            "内存不足",
        )
    ):
        return _info(AgentErrorCode.AGENT_RESOURCE_EXHAUSTED, message, failover=True, retryable=True)
    if any(
        marker in text
        for marker in (
            "model invocation",
            "model call failed",
            "inference failed",
            "model error",
            "模型调用",
            "模型异常",
            "推理失败",
        )
    ):
        return _info(AgentErrorCode.MODEL_INVOCATION_ERROR, message, failover=False, retryable=False)
    if "agent is not ready" in text or "service unavailable" in text:
        return _info(AgentErrorCode.AGENT_NOT_READY, message, failover=True, retryable=True)
    if "late response ignored" in text:
        return _info(AgentErrorCode.AGENT_LATE_RESPONSE, message, failover=False, retryable=False)

    if isinstance(error, requests.exceptions.RequestException):
        return _info(AgentErrorCode.AGENT_PROTOCOL_ERROR, message, failover=False, retryable=False)

    return _info(AgentErrorCode.AGENT_BUSINESS_ERROR, message, failover=False, retryable=False)


def is_agent_unavailable_error(error: Any) -> bool:
    return classify_agent_error(error).failover


def _classify_response_payload(error: Any, fallback_message: str) -> Optional[AgentErrorInfo]:
    payload = getattr(error, "response_payload", None)
    if not isinstance(payload, Mapping):
        return None

    code = payload.get("error_code")
    message = str(payload.get("error") or payload.get("message") or fallback_message)
    if code:
        code = str(code)
        return AgentErrorInfo(
            code=code,
            category=_category_for_code(code),
            message=message,
            failover=code in FAILOVER_ERROR_CODES,
            retryable=code in FAILOVER_ERROR_CODES,
        )

    return classify_agent_error(message)


def _info(code: AgentErrorCode, message: str, *, failover: bool, retryable: bool) -> AgentErrorInfo:
    return AgentErrorInfo(
        code=code.value,
        category=_category_for_code(code.value),
        message=message,
        failover=failover,
        retryable=retryable,
    )


def _category_for_code(code: str) -> str:
    if code == AgentErrorCode.AGENT_RESOURCE_EXHAUSTED.value:
        return "resource"
    if code == AgentErrorCode.MODEL_INVOCATION_ERROR.value:
        return "model"
    if code in FAILOVER_ERROR_CODES:
        return "system"
    if code == AgentErrorCode.AGENT_BUSINESS_ERROR.value:
        return "business"
    return "protocol"
