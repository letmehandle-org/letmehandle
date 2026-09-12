"""Errors carry facts as attributes, because callers branch on them.

Each of these asserts the attribute rather than the message. A caller that has to parse a
sentence to decide what to do has turned a human-readable string into an interface, and the
next person to improve the wording breaks it.
"""

from __future__ import annotations

import pytest

from letmehandle.domain.errors import (
    CapabilityNotSupportedError,
    DomainError,
    IllegalTransitionError,
    InvariantError,
    NotAuthorisedError,
    ProviderError,
)

EVERY_ERROR = [
    InvariantError,
    IllegalTransitionError,
    CapabilityNotSupportedError,
    NotAuthorisedError,
    ProviderError,
]


@pytest.mark.parametrize("kind", EVERY_ERROR)
def test_every_domain_error_can_be_caught_as_one(kind: type[DomainError]) -> None:
    # The property that lets application code catch the product's failures without also
    # catching a vendor's.
    assert issubclass(kind, DomainError)


def test_an_illegal_transition_names_both_states() -> None:
    error = IllegalTransitionError("here", "there")
    assert error.current == "here"
    assert error.requested == "there"
    assert "here" in str(error) and "there" in str(error)


def test_an_unsupported_capability_names_the_capability() -> None:
    error = CapabilityNotSupportedError("a transport", "can_bridge_human")
    assert error.provider == "a transport"
    assert error.capability == "can_bridge_human"


def test_a_refusal_names_the_action() -> None:
    error = NotAuthorisedError("agree to a delivery")
    assert error.action == "agree to a delivery"


@pytest.mark.parametrize("retryable", [True, False])
def test_a_provider_failure_says_whether_trying_again_is_worthwhile(retryable: bool) -> None:
    # Without this, every caller invents its own guess about which failures are worth a retry,
    # and they disagree. Retrying a non-idempotent operation is a defect, not a nuisance.
    error = ProviderError("a speech provider", "the stream closed", retryable=retryable)
    assert error.retryable is retryable
    assert error.provider == "a speech provider"
    assert error.reason == "the stream closed"


def test_not_authorised_and_not_supported_are_different_failures() -> None:
    # One means the assistant may not; the other means it cannot. Collapsing them would make a
    # permission decision look like a technical fault, and the right response to each differs.
    assert not issubclass(NotAuthorisedError, CapabilityNotSupportedError)
    assert not issubclass(CapabilityNotSupportedError, NotAuthorisedError)
