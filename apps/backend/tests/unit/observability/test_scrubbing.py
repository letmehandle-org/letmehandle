"""Nothing personal or secret leaves the process in a log line, however it was put there."""

from __future__ import annotations

import io
import json
import logging
from typing import TYPE_CHECKING

import pytest
import structlog

from letmehandle.config.settings import LogFormat
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.observability.logging import configure_logging
from letmehandle.observability.scrubbing import REDACTED, exception_outline, scrub_event
from tests.support.config import make_settings

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

NUMBER = "+12025550123"
TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.c2lnbmF0dXJl"
SAID = "my account number is in the drawer"


@pytest.fixture
def written() -> Iterator[Callable[[], list[dict[str, object]]]]:
    """Logging as a deployment configures it, written where the test can read it, then put back.

    Both of its outputs are pointed at one buffer after configuring, leaving every processor as
    configured: the stream standard error was when configured may be closed by the time a test
    writes, which a capture of the process's output cannot help with.
    """
    configure_logging(make_settings(log_format=LogFormat.JSON, log_level="debug"))
    buffer = io.StringIO()
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=buffer))
    [handler] = logging.getLogger().handlers
    assert isinstance(handler, logging.StreamHandler)
    handler.setStream(buffer)
    yield lambda: [json.loads(line) for line in buffer.getvalue().splitlines() if line]
    configure_logging(make_settings())


def _fail_with(error: Exception) -> None:
    raise error


def _fail_looking_up_the_caller() -> None:
    try:
        _fail_with(LookupError(f"no user with {NUMBER}"))
    except LookupError as inner:
        raise RuntimeError(SAID) from inner


def test_a_sensitive_field_is_removed_however_it_is_spelled_and_however_deep() -> None:
    event = scrub_event(
        None,
        "info",
        {
            "event": "call.heard",
            "Caller-Number": NUMBER,
            "detail": {"transcript": [SAID], "nested": {"displayName": "Sam"}},
            "attempts": [{"token": TOKEN, "outcome": "delivered"}],
        },
    )

    assert event == {
        "event": "call.heard",
        "Caller-Number": REDACTED,
        "detail": {"transcript": REDACTED, "nested": {"displayName": REDACTED}},
        "attempts": [{"token": REDACTED, "outcome": "delivered"}],
    }


def test_a_number_or_a_credential_inside_innocent_text_is_replaced() -> None:
    event = scrub_event(
        None,
        "warning",
        {
            "event": "telephony.webhook.rejected",
            "reason": f"no call from {NUMBER} with Bearer {TOKEN}",
            "values": (f"forwarded {NUMBER}", 3, None, True),
        },
    )

    assert NUMBER not in json.dumps(event)
    assert TOKEN not in json.dumps(event)
    assert event["values"] == [f"forwarded {REDACTED}", 3, None, True]


def test_a_masked_number_is_left_alone_because_it_identifies_nobody() -> None:
    masked = PhoneNumber.parse(NUMBER).masked

    assert scrub_event(None, "info", {"event": "x", "shown": masked})["shown"] == masked


def test_a_value_object_is_rendered_and_checked_rather_than_trusted() -> None:
    class Leaky:
        def __str__(self) -> str:
            return f"call from {NUMBER}"

    assert scrub_event(None, "info", {"event": "x", "what": Leaky()})["what"] == (
        f"call from {REDACTED}"
    )


def test_a_structure_that_refers_to_itself_is_cut_off_rather_than_followed_forever() -> None:
    looping: list[object] = []
    looping.append(looping)

    scrubbed = scrub_event(None, "info", {"event": "x", "loop": looping})

    assert REDACTED in json.dumps(scrubbed)


def test_an_exception_is_logged_by_its_type_and_frames_never_its_message(
    written: Callable[[], list[dict[str, object]]],
) -> None:
    try:
        _fail_looking_up_the_caller()
    except RuntimeError:
        structlog.get_logger("letmehandle.test").exception("call.failed")

    [line] = written()
    rendered = json.dumps(line)
    outline = line["exception"]
    assert isinstance(outline, dict)
    assert outline["type"] == "RuntimeError"
    assert outline["causes"] == ["LookupError"]
    assert any("test_scrubbing.py" in frame for frame in outline["frames"])
    assert NUMBER not in rendered
    assert SAID not in rendered


def test_a_standard_library_line_passes_the_same_scrubber(
    written: Callable[[], list[dict[str, object]]],
) -> None:
    library = logging.getLogger("some.library")
    try:
        _fail_with(ValueError(SAID))
    except ValueError:
        library.error("request for %s failed", NUMBER, exc_info=True)

    [line] = written()
    outline = line["exception"]
    assert line["event"] == f"request for {REDACTED} failed"
    assert isinstance(outline, dict)
    assert outline["type"] == "ValueError"
    assert SAID not in json.dumps(line)


def test_an_exception_handed_over_directly_is_outlined_too(
    written: Callable[[], list[dict[str, object]]],
) -> None:
    structlog.get_logger("letmehandle.test").warning("x", exc_info=KeyError(NUMBER))

    [line] = written()
    assert line["exception"] == {"type": "KeyError", "causes": [], "frames": []}


def test_a_line_logged_without_an_exception_gains_no_outline(
    written: Callable[[], list[dict[str, object]]],
) -> None:
    logging.getLogger("some.library").warning("plain", exc_info=False)
    structlog.get_logger("letmehandle.test").info("plain")
    # Asked for, with no exception being handled: there is nothing to outline.
    structlog.get_logger("letmehandle.test").info("plain", exc_info=True)

    assert all("exception" not in each for each in written())


def test_an_exception_whose_chain_loops_is_outlined_once() -> None:
    first = ValueError("a")
    second = KeyError("b")
    first.__context__ = second
    second.__context__ = first

    assert exception_outline(first)["causes"] == ["KeyError", "ValueError"]
