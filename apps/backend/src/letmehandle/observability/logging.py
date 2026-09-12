"""Structured logging, configured once."""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import TYPE_CHECKING, Final

import structlog

if TYPE_CHECKING:
    from collections.abc import Mapping

    from structlog.typing import EventDict, Processor, WrappedLogger

from letmehandle.config.settings import LogFormat, Settings

# The identifier that ties every line produced while handling one request together. A context
# variable rather than an argument, because threading it through every call signature is how
# it ends up omitted from the line that mattered.
correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def add_correlation_id(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    """Attach the current correlation id, when there is one."""
    current = correlation_id.get()
    if current is not None:
        event_dict["correlation_id"] = current
    return event_dict


# Libraries that log what passes through them, and the lowest level each may log at whatever the
# process is set to. A debugging session is exactly when a log is copied somewhere it should not go.
_CONTENT_LOGGERS: Final[Mapping[str, int]] = {
    # Request headers and frame text at debug: the speech service's key and what somebody said.
    "websockets": logging.WARNING,
    # The model client's request options at debug, which carry the whole prompt and transcript.
    "openai": logging.WARNING,
    # Requests and their headers at debug.
    "httpx": logging.WARNING,
    # The formatted request at debug, and up to 200 characters of a tool's unparseable arguments
    # at warning, which a model may have filled with the caller's words.
    "strands": logging.ERROR,
}


def configure_logging(settings: Settings) -> None:
    """Set up logging for the process.

    Called once, at startup. Configuring it lazily or in more than one place is how two halves
    of an application end up logging in two different formats.
    """
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        add_correlation_id,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if settings.log_format is LogFormat.JSON
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    structlog.configure(
        processors=[*shared, structlog.processors.format_exc_info, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[settings.log_level.upper()]
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    # Anything logging through the standard library — uvicorn, sqlalchemy, a dependency —
    # goes through the same pipeline, so one call cannot arrive in two formats.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=logging.getLevelNamesMapping()[settings.log_level.upper()],
        force=True,
    )

    # Held at their floors, or above them when the process is set higher.
    for name, floor in _CONTENT_LOGGERS.items():
        logging.getLogger(name).setLevel(max(floor, logging.getLogger().level))


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """A logger bound to a module name."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
