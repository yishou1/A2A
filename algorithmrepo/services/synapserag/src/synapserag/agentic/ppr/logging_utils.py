import logging
from dataclasses import dataclass, field, replace
from typing import Dict, Optional, Sequence


def shorten_text(text: str, limit: int = 96) -> str:
    """Condense long passages/phrases for logging."""
    if not isinstance(text, str):
        text = str(text)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


@dataclass
class AgenticLogContext:
    """Shared context for agentic logging."""

    query: str = ""
    iteration: Optional[int] = None
    max_iterations: Optional[int] = None
    extras: Dict[str, str] = field(default_factory=dict)

    def with_updates(self, **kwargs) -> "AgenticLogContext":
        """Return a copy with selected fields overwritten."""
        return replace(self, **kwargs)

    def with_extras(self, **extras) -> "AgenticLogContext":
        """Merge extra context values."""
        merged = dict(self.extras)
        for key, value in extras.items():
            if value is None:
                continue
            merged[key] = str(value)
        return replace(self, extras=merged)


@dataclass
class AgenticLogOptions:
    """Control how agentic logs are emitted."""

    level: int = logging.INFO
    log_query: bool = True
    context: Optional[AgenticLogContext] = None

    def derive(self, **kwargs) -> "AgenticLogOptions":
        """Return a copy with selected overrides."""
        return replace(self, **kwargs)


class AgenticLogFormatter:
    """Helper that standardizes agentic log messages."""

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)

    def log_block(
        self,
        title: str,
        lines: Optional[Sequence[str]] = None,
        options: Optional[AgenticLogOptions] = None,
    ) -> None:
        opts = options or AgenticLogOptions()
        if not self.logger.isEnabledFor(opts.level):
            return

        header = self._build_header(title, opts)
        if not lines:
            self.logger.log(opts.level, header)
            return

        message = "\n".join([header] + [f"  - {line}" for line in lines])
        self.logger.log(opts.level, message)

    def _build_header(self, title: str, options: AgenticLogOptions) -> str:
        prefix_parts = []
        context = options.context

        if context:
            if context.iteration is not None:
                if context.max_iterations:
                    prefix_parts.append(
                        f"[iter {context.iteration}/{context.max_iterations}]"
                    )
                else:
                    prefix_parts.append(f"[iter {context.iteration}]")

            if options.log_query and context.query:
                prefix_parts.append(f'Q="{shorten_text(context.query)}"')

            if context.extras:
                extras = " ".join(f"{k}={v}" for k, v in context.extras.items())
                if extras:
                    prefix_parts.append(f"[{extras}]")

        prefix = " ".join(prefix_parts).strip()
        if prefix:
            return f"{prefix} {title}"
        return title
