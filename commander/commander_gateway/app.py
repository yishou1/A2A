from __future__ import annotations

import secrets

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from commander_gateway.config import GatewayConfig
from commander_gateway.errors import GatewayError
from commander_gateway.schemas import CommanderProjectionV1, WorkflowSubmitV1
from commander_gateway.service import GatewayService


def build_gateway_app(
    *,
    config: GatewayConfig | None = None,
    service: GatewayService | None = None,
) -> FastAPI:
    gateway_config = config or GatewayConfig.from_env()
    gateway_service = service or GatewayService(gateway_config)
    app = FastAPI(title="AMOS Commander Gateway", version="1.0.0")
    app.state.gateway_config = gateway_config
    app.state.gateway_service = gateway_service

    @app.exception_handler(GatewayError)
    async def gateway_error_handler(_request: Request, exc: GatewayError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail()},
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "INVALID_REQUEST",
                    "message": "request validation failed",
                    "retriable": False,
                    "errors": jsonable_encoder(exc.errors()),
                }
            },
        )

    @app.exception_handler(ResponseValidationError)
    async def response_validation_error_handler(
        _request: Request, _exc: ResponseValidationError
    ):
        return JSONResponse(
            status_code=500,
            content={
                "detail": {
                    "code": "INVALID_RESPONSE",
                    "message": "Gateway response validation failed",
                    "retriable": False,
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(_request: Request, exc: StarletteHTTPException):
        code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
        if exc.status_code == 405:
            code = "METHOD_NOT_ALLOWED"
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": {
                    "code": code,
                    "message": str(exc.detail),
                    "retriable": False,
                }
            },
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def internal_error_handler(_request: Request, _exc: Exception):
        return JSONResponse(
            status_code=500,
            content={
                "detail": {
                    "code": "INTERNAL_ERROR",
                    "message": "Gateway internal error",
                    "retriable": False,
                }
            },
        )

    def require_control_token(request: Request) -> None:
        expected = gateway_config.api_token
        if not expected:
            return
        authorization = request.headers.get("Authorization", "")
        scheme, separator, supplied = authorization.partition(" ")
        if not separator or scheme.casefold() != "bearer":
            supplied = ""
        if not supplied or not secrets.compare_digest(supplied, expected):
            raise GatewayError(
                "UNAUTHORIZED",
                "valid Bearer token required",
                401,
                False,
                {"WWW-Authenticate": "Bearer"},
            )

    control = Depends(require_control_token)

    @app.get("/gateway/v1/health")
    async def health():
        status_code, payload = gateway_service.health()
        return JSONResponse(status_code=status_code, content=payload)

    @app.post(
        "/gateway/v1/workflows",
        response_model=CommanderProjectionV1,
        status_code=202,
        dependencies=[control],
    )
    async def submit_workflow(request: WorkflowSubmitV1):
        return gateway_service.submit(request)

    @app.get(
        "/gateway/v1/workflows/{workflow_id}",
        response_model=CommanderProjectionV1,
        dependencies=[control],
    )
    async def get_workflow(workflow_id: str):
        return gateway_service.get_projection(workflow_id)

    @app.post(
        "/gateway/v1/workflows/{workflow_id}/resume",
        response_model=CommanderProjectionV1,
        status_code=202,
        dependencies=[control],
    )
    async def resume_workflow(workflow_id: str):
        return gateway_service.resume(workflow_id)

    @app.get(
        "/gateway/v1/workflows/{workflow_id}/work-list",
        dependencies=[control],
    )
    async def get_work_list(workflow_id: str):
        return {
            "workflow_id": workflow_id,
            "work_list": gateway_service.get_work_list(workflow_id),
        }

    @app.get(
        "/gateway/v1/workflows/{workflow_id}/trace",
        dependencies=[control],
    )
    async def get_trace(workflow_id: str):
        return {
            "workflow_id": workflow_id,
            "trace": gateway_service.get_trace(workflow_id),
        }

    @app.get("/gateway/v1/packages/{package_id}")
    async def get_package(package_id: str):
        body, checksum = gateway_service.store.read_package(package_id)
        return Response(
            content=body,
            media_type="application/json",
            headers={
                "ETag": f'"{checksum}"',
                "X-Checksum-SHA256": checksum,
            },
        )

    return app


app = build_gateway_app()
