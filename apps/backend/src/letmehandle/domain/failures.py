"""One account of what a failure means, whoever raised it.

Every caller that catches an exception has to decide the same four things: is it worth trying
again, is it something to tell the person using the product, is it something whoever runs the
deployment has to act on, and is it a defect. Decided at each catch, those answers drift apart,
and the first place they disagree is a retry of something that must not be repeated. So they are
decided once, here, from the kind of failure, and every error the product defines says which kind
it is.

An error states its kind as `failure_kind`. Anything that does not — a library's exception, a
programming mistake — is a defect, except the two failures of the world outside that the standard
library names: a wait that ran out, and a connection that could not be made.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class FailureKind(StrEnum):
    """What went wrong, in terms bounded enough to count and to branch on."""

    # A bounded wait ran out. The same request may well succeed.
    TIMEOUT = "timeout"
    # Something the product depends on could not be reached, or said it was not able just now.
    UNAVAILABLE = "unavailable"
    # Something the product depends on answered, and the answer was no. Asking again gets it again.
    REFUSED = "refused"
    # Not asked at all, because that dependency has been failing and is being given time.
    CIRCUIT_OPEN = "circuit_open"
    # The request itself was not valid: a value the rules do not allow, a step not offered here.
    INVALID = "invalid"
    # Whoever asked may not: not signed in, or not permitted.
    NOT_PERMITTED = "not_permitted"
    # Asked too often. Told when to ask again rather than retried here.
    RATE_LIMITED = "rate_limited"
    # What was asked about does not exist for whoever asked.
    NOT_FOUND = "not_found"
    # What was asked for has already happened, or the thing it was about has moved on.
    CONFLICT = "conflict"
    # Stored ciphertext would not open: a key is missing or a record was altered.
    SEALED = "sealed"
    # Nothing above: a mistake in the product, which no retry and no message to anybody fixes.
    DEFECT = "defect"


@dataclass(frozen=True, slots=True)
class Failure:
    """What a kind of failure asks of whoever caught it.

    `retryable` says trying the same operation again may succeed; whether it is safe to is the
    operation's own property, and `retry_idempotent` asks for both. `user_visible` says the person
    using the product is told, as a response or a state they can see. `needs_attention` says
    somebody running the deployment has to act, which is what an error-level log line means here;
    everything else is logged as a warning and counted.
    """

    kind: FailureKind
    retryable: bool
    user_visible: bool
    needs_attention: bool

    @property
    def is_defect(self) -> bool:
        return self.kind is FailureKind.DEFECT


_FAILURES: Final = {
    failure.kind: failure
    for failure in (
        Failure(FailureKind.TIMEOUT, retryable=True, user_visible=False, needs_attention=False),
        Failure(FailureKind.UNAVAILABLE, retryable=True, user_visible=False, needs_attention=False),
        Failure(FailureKind.REFUSED, retryable=False, user_visible=False, needs_attention=False),
        # Refused here rather than there, so nothing is gained by asking again before it closes.
        Failure(
            FailureKind.CIRCUIT_OPEN, retryable=False, user_visible=False, needs_attention=False
        ),
        Failure(FailureKind.INVALID, retryable=False, user_visible=True, needs_attention=False),
        Failure(
            FailureKind.NOT_PERMITTED, retryable=False, user_visible=True, needs_attention=False
        ),
        Failure(
            FailureKind.RATE_LIMITED, retryable=False, user_visible=True, needs_attention=False
        ),
        Failure(FailureKind.NOT_FOUND, retryable=False, user_visible=True, needs_attention=False),
        Failure(FailureKind.CONFLICT, retryable=False, user_visible=True, needs_attention=False),
        Failure(FailureKind.SEALED, retryable=False, user_visible=False, needs_attention=True),
        Failure(FailureKind.DEFECT, retryable=False, user_visible=False, needs_attention=True),
    )
}


def failure_of(kind: FailureKind) -> Failure:
    """What `kind` asks of whoever caught it."""
    return _FAILURES[kind]


def classify(error: BaseException) -> Failure:
    """What `error` means. Never raises, and never reads the error's message."""
    declared = getattr(error, "failure_kind", None)
    if isinstance(declared, FailureKind):
        return _FAILURES[declared]
    # Before the connection failures: a timeout is an operating-system error too.
    if isinstance(error, TimeoutError):
        return _FAILURES[FailureKind.TIMEOUT]
    if isinstance(error, ConnectionError):
        return _FAILURES[FailureKind.UNAVAILABLE]
    return _FAILURES[FailureKind.DEFECT]
