"""What a kind of failure asks of whoever catches it, decided once for every error (D-038)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class FailureKind(StrEnum):
    """What went wrong, in terms bounded enough to count and to branch on."""

    # A bounded wait ran out.
    TIMEOUT = "timeout"
    # A dependency could not be reached, or said it was not able just now.
    UNAVAILABLE = "unavailable"
    # A dependency answered no, and asking again gets the same answer.
    REFUSED = "refused"
    # Not asked at all, because that dependency has been failing.
    CIRCUIT_OPEN = "circuit_open"
    # The request was not valid.
    INVALID = "invalid"
    # Whoever asked is not signed in, or not permitted.
    NOT_PERMITTED = "not_permitted"
    # Asked too often.
    RATE_LIMITED = "rate_limited"
    # What was asked about does not exist for whoever asked.
    NOT_FOUND = "not_found"
    # What was asked for has already happened, or its subject has moved on.
    CONFLICT = "conflict"
    # Stored ciphertext would not open.
    SEALED = "sealed"
    # A mistake in the product.
    DEFECT = "defect"


@dataclass(frozen=True, slots=True)
class Failure:
    """Whether a kind of failure is retryable, shown to the user, or needs an operator."""

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
    """What `error` means, from its declared kind or its standard-library type; never raises."""
    declared = getattr(error, "failure_kind", None)
    if isinstance(declared, FailureKind):
        return _FAILURES[declared]
    # A timeout is also an operating-system error, so it is checked before connection failures.
    if isinstance(error, TimeoutError):
        return _FAILURES[FailureKind.TIMEOUT]
    if isinstance(error, ConnectionError):
        return _FAILURES[FailureKind.UNAVAILABLE]
    return _FAILURES[FailureKind.DEFECT]
