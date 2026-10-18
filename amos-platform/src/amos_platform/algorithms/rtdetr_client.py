"""Small HTTP adapter for the standalone battlefield RT-DETR service."""

from __future__ import annotations

import json
import os
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid


ALGORITHM_ID = "battlefield_rtdetr_detector"
ALGORITHM_VERSION = "1.0.0"
DEFAULT_ENDPOINT = "http://127.0.0.1:9020"


class RTDETRServiceError(RuntimeError):
    """A predictable failure at the AMOS-to-algorithm service boundary."""

    def __init__(self, message: str, *, status_code: int = 502, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.details = details or {}


class RTDETRClient:
    """Invoke one released AMOS raster image through the RT-DETR contract."""

    def __init__(self, endpoint: str | None = None, timeout_sec: float | None = None):
        self.endpoint = (endpoint or os.getenv("AMOS_RTDETR_ENDPOINT") or DEFAULT_ENDPOINT).rstrip("/")
        configured_timeout = timeout_sec if timeout_sec is not None else os.getenv("AMOS_RTDETR_TIMEOUT_SEC", "60")
        self.timeout_sec = max(1.0, float(configured_timeout))

    def detect(self, media: dict[str, Any], image_url: str) -> dict[str, Any]:
        request_id = f"amos-rtdetr-{uuid.uuid4().hex[:16]}"
        trace_id = str(media.get("capture_id") or request_id)
        sensor_id = str(media.get("sensor_instance_id") or media.get("sensor_id") or media.get("media_id") or "AMOS-MEDIA")
        modality = str(media.get("modality") or "eo_ir")
        payload = {
            "request_id": request_id,
            "trace_id": trace_id,
            "algorithm_id": ALGORITHM_ID,
            "version": ALGORITHM_VERSION,
            "inputs": {
                "frames": [{
                    "sensor_id": sensor_id,
                    "modality": modality,
                    "payload": {"image_uri": image_url},
                    "metadata": {
                        "media_id": str(media.get("media_id") or ""),
                        "checksum": str(media.get("checksum") or ""),
                    },
                }],
            },
            "params": {},
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.endpoint}/predict",
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_sec) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            details = _read_http_error(exc)
            raise RTDETRServiceError(
                f"RT-DETR 服务返回 HTTP {exc.code}",
                status_code=502,
                details=details,
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise RTDETRServiceError(
                "RT-DETR 调用超时，请确认服务已启动或适当提高超时设置",
                status_code=504,
            ) from exc
        except (URLError, ConnectionError, OSError) as exc:
            raise RTDETRServiceError(
                "无法连接 RT-DETR 服务，请检查检测服务及容器网络",
                status_code=503,
                details={"endpoint": self.endpoint, "reason": str(exc)},
            ) from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RTDETRServiceError("RT-DETR 服务返回了无法解析的响应") from exc

        if not isinstance(result, dict):
            raise RTDETRServiceError("RT-DETR 服务响应格式无效")
        if result.get("ok") is not True:
            error = result.get("error") or {}
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise RTDETRServiceError(
                message or "RT-DETR 推理失败",
                details={"algorithm_response": result},
            )
        outputs = result.get("outputs") or {}
        if not isinstance(outputs, dict):
            raise RTDETRServiceError("RT-DETR 输出格式无效")
        detections = outputs.get("detections") or []
        if not isinstance(detections, list):
            raise RTDETRServiceError("RT-DETR 输出缺少 detections 数组")
        return {
            "request_id": result.get("request_id") or request_id,
            "trace_id": result.get("trace_id") or trace_id,
            "algorithm_id": result.get("algorithm_id") or ALGORITHM_ID,
            "version": result.get("version") or ALGORITHM_VERSION,
            "detections": detections,
            "count": int(outputs.get("count", len(detections))),
            "runtime": outputs.get("runtime") or {},
            "usage": result.get("usage") or {},
        }


def _read_http_error(exc: HTTPError) -> dict[str, Any]:
    try:
        payload = json.loads(exc.read().decode("utf-8"))
        return payload if isinstance(payload, dict) else {"response": payload}
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return {}
