"""Structured logging, configured once."""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from typing import TYPE_CHECKING, Final

import structlog

if TYPE_CHECKING:
    from collections.abc import Mapping

    from structlog.typing import EventDict, Processor, WrappedLogger

from letmehandle.config.settings import LogFormat, Settings
from letmehandle.domain.failures import classify
from letmehandle.observability.scrubbing import outline_exception, scrub_event
from letmehandle.observability.tracing import traceable_call_id

# The id tying together every line logged while handling one request.
correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def add_correlation_id(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    """Attach the current correlation id, when there is one."""
    current = correlation_id.get()
    if current is not None:
        event_dict["correlation_id"] = current
    return event_dict


# Libraries that log sensitive data, each with the lowest level it may log at.
_CONTENT_LOGGERS: Final[Mapping[str, int]] = {
    # Request headers and frame text at debug: the speech service's key and what somebody said.
    "websockets": logging.WARNING,
    # The model client's request options at debug, which carry the whole prompt and transcript.
    "openai": logging.WARNING,
    # Requests and their headers at debug.
    "httpx": logging.WARNING,
    # The connection layer, whose debug response headers carry provider account ids.
    "httpcore": logging.WARNING,
    # Formatted requests at debug and a tool's raw arguments at warning.
    "strands": logging.ERROR,
    # Unknown tool names, which a caller can dictate, logged verbatim at error.
    "strands.tools.executors": logging.CRITICAL,
}


def configure_logging(settings: Settings) -> None:
    """Set up logging for the process, once, at startup."""
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        add_correlation_id,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]
    # Last before rendering, on both paths, so nothing added along the way escapes them.
    finishing: list[Processor] = [outline_exception, scrub_event]

    if settings.log_format is LogFormat.JSON:
        renderer: Processor = structlog.processors.JSONRenderer()
    else:
        # The console renderer expects text, so the exception outline is written as a line first.
        finishing = [*finishing, _exception_as_text]
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    level = logging.getLevelNamesMapping()[settings.log_level.upper()]

    structlog.configure(
        processors=[*shared, *finishing, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    # Standard-library logging goes through the same processors, including the scrubber.
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                *finishing,
                renderer,
            ],
        )
    )
    logging.basicConfig(handlers=[handler], level=level, force=True)

    # Held at their floors, or above them when the process is set higher.
    for name, floor in _CONTENT_LOGGERS.items():
        logging.getLogger(name).setLevel(max(floor, logging.getLogger().level))


def bind_call(call_id: str, request: str | None) -> None:
    """Bind a call's id and its request's id to every line this task and its children log."""
    structlog.contextvars.bind_contextvars(call_id=traceable_call_id(call_id))
    if request is not None:
        correlation_id.set(request)


def log_failure(
    logger: structlog.stdlib.BoundLogger, event: str, error: BaseException, **fields: object
) -> None:
    """Log a handled failure by type and kind, at error when actionable, never its message."""
    failure = classify(error)
    write = logger.error if failure.needs_attention else logger.warning
    write(event, error=type(error).__name__, kind=failure.kind.value, **fields)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """A logger bound to a module name."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger


def _exception_as_text(_logger: object, _method: str, event_dict: EventDict) -> EventDict:
    outline = event_dict.get("exception")
    if isinstance(outline, dict):
        event_dict["exception"] = json.dumps(outline)
    return event_dict
