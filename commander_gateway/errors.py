from __future__ import annotations


class GatewayError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int,
        retriable: bool = False,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retriable = retriable
        self.headers = headers

    def detail(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "retriable": self.retriable,
        }


class UpstreamError(GatewayError):
    """Normalized error returned by an AMOS or Commander client."""
