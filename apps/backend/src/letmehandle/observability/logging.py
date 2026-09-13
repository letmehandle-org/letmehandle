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
from letmehandle.domain.failures import classify
from letmehandle.observability.scrubbing import outline_exception, scrub_event
from letmehandle.observability.tracing import traceable_call_id

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
    # The connection layer beneath it, which logs response headers at debug: a provider's request
    # and account identifiers ride in those.
    "httpcore": logging.WARNING,
    # The formatted request at debug, and up to 200 characters of a tool's unparseable arguments
    # at warning, which a model may have filled with the caller's words.
    "strands": logging.ERROR,
    # A tool name the model asked for and the registry does not have, verbatim, at error. The model
    # wrote that name, and a caller can dictate it. The agent records the request itself, as a
    # refusal, and raises any tool that failed, so nothing below critical here is lost.
    "strands.tools.executors": logging.CRITICAL,
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
    # Last before rendering, on both paths, so nothing added along the way escapes them.
    finishing: list[Processor] = [outline_exception, scrub_event]

    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if settings.log_format is LogFormat.JSON
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )
    level = logging.getLevelNamesMapping()[settings.log_level.upper()]

    structlog.configure(
        processors=[*shared, *finishing, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    # Anything logging through the standard library — uvicorn, sqlalchemy, a dependency — goes
    # through the same processors, so one call cannot arrive in two formats, and no library's line
    # passes the scrubber by not being structlog's.
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
    """Put a call's id, and the id of the request it arrived on, on every line this task logs.

    For the task that runs one call, at its start. A task begins with a copy of the context it was
    created in, so what is bound here stays with that call's run and the tasks the run starts, and
    never reaches another call's lines.
    """
    structlog.contextvars.bind_contextvars(call_id=traceable_call_id(call_id))
    if request is not None:
        correlation_id.set(request)


def log_failure(
    logger: structlog.stdlib.BoundLogger, event: str, error: BaseException, **fields: object
) -> None:
    """Log a failure that was caught and handled, at the level its kind deserves.

    An error when somebody running the deployment has to act on it — a defect, a record that will
    not open — and a warning otherwise, since a timeout on one call is counted, and a dependency
    failing on every call opens a circuit that says so at error. Never the message or a traceback:
    the type and the kind find it, and a message can carry anything.
    """
    failure = classify(error)
    write = logger.error if failure.needs_attention else logger.warning
    write(event, error=type(error).__name__, kind=failure.kind.value, **fields)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """A logger bound to a module name."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
