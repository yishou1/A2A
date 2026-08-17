"""Single-process AMOS runtime facade."""

from amos_platform.runtime.mode import PlatformMode, read_platform_mode
from amos_platform.runtime.platform_runtime import PlatformRuntime, get_platform_runtime
from amos_platform.runtime.run_context import RunContext

__all__ = [
    "PlatformMode",
    "PlatformRuntime",
    "RunContext",
    "get_platform_runtime",
    "read_platform_mode",
]
