from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _base_url(value: str, name: str) -> str:
    normalized = value.strip().rstrip("/")
    if not normalized.startswith(("http://", "https://")):
        raise ValueError(f"{name} must be an http(s) URL")
    return normalized


@dataclass(frozen=True)
class GatewayConfig:
    amos_base_url: str = "http://127.0.0.1:5000"
    commander_base_url: str = "http://127.0.0.1:8021"
    public_base_url: str = "http://127.0.0.1:8030"
    state_dir: Path = Path(".a2a_state/commander_gateway")
    api_token: str = ""
    commander_token: str = ""
    request_timeout_sec: float = 5.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "amos_base_url", _base_url(self.amos_base_url, "AMOS_BASE_URL"))
        object.__setattr__(
            self,
            "commander_base_url",
            _base_url(self.commander_base_url, "COMMANDER_BASE_URL"),
        )
        object.__setattr__(
            self,
            "public_base_url",
            _base_url(self.public_base_url, "GATEWAY_PUBLIC_BASE_URL"),
        )
        object.__setattr__(self, "state_dir", Path(self.state_dir))
        if self.request_timeout_sec <= 0:
            raise ValueError("GATEWAY_REQUEST_TIMEOUT_SEC must be greater than zero")

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        raw_timeout = os.getenv("GATEWAY_REQUEST_TIMEOUT_SEC", "5.0")
        try:
            timeout = float(raw_timeout)
        except ValueError as exc:
            raise ValueError("GATEWAY_REQUEST_TIMEOUT_SEC must be a number") from exc
        return cls(
            amos_base_url=os.getenv("AMOS_BASE_URL", "http://127.0.0.1:5000"),
            commander_base_url=os.getenv(
                "COMMANDER_BASE_URL", "http://127.0.0.1:8021"
            ),
            public_base_url=os.getenv(
                "GATEWAY_PUBLIC_BASE_URL", "http://127.0.0.1:8030"
            ),
            state_dir=Path(
                os.getenv("GATEWAY_STATE_DIR", ".a2a_state/commander_gateway")
            ),
            api_token=os.getenv("GATEWAY_API_TOKEN", ""),
            commander_token=os.getenv("COMMANDER_TOKEN", ""),
            request_timeout_sec=timeout,
        )
