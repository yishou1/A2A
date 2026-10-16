"""Adapters for invoking independently deployed algorithm services."""

from amos_platform.algorithms.rtdetr_client import (
    RTDETRClient,
    RTDETRServiceError,
)

__all__ = ["RTDETRClient", "RTDETRServiceError"]
