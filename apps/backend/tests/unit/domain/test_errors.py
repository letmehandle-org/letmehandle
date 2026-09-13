"""Errors carry facts as attributes, because callers branch on them.

Each of these asserts the attribute rather than the message. A caller that has to parse a
sentence to decide what to do has turned a human-readable string into an interface, and the
next person to improve the wording breaks it.
"""

from __future__ import annotations

import pytest

from letmehandle.domain.errors import (
    AlreadyRecordedError,
    CapabilityNotSupportedError,
    DecryptionError,
    DomainError,
    IllegalTransitionError,
    InvariantError,
    ProviderError,
    RecordNotFoundError,
    UnknownKeyError,
)

EVERY_ERROR = [
    InvariantError,
    IllegalTransitionError,
    CapabilityNotSupportedError,
    ProviderError,
    RecordNotFoundError,
    AlreadyRecordedError,
    DecryptionError,
    UnknownKeyError,
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


@pytest.mark.parametrize("retryable", [True, False])
def test_a_provider_failure_says_whether_trying_again_is_worthwhile(retryable: bool) -> None:
    # Without this, every caller invents its own guess about which failures are worth a retry,
    # and they disagree. Retrying a non-idempotent operation is a defect, not a nuisance.
    error = ProviderError("a speech provider", "the stream closed", retryable=retryable)
    assert error.retryable is retryable
    assert error.provider == "a speech provider"
    assert error.reason == "the stream closed"


def test_a_missing_record_names_what_was_looked_for() -> None:
    error = RecordNotFoundError("call", "call-1")
    assert (error.kind, error.identifier) == ("call", "call-1")


def test_a_second_recording_names_what_was_recorded() -> None:
    error = AlreadyRecordedError("summary", "call-1")
    assert (error.kind, error.identifier) == ("summary", "call-1")


def test_a_missing_key_is_a_decryption_failure_with_its_own_remedy() -> None:
    # Caught as a decryption failure by anything that only cares that the bytes did not open,
    # and told apart by an operator who needs to know that a key has to be put back.
    refused, missing = DecryptionError("key-a"), UnknownKeyError("key-a")
    assert isinstance(missing, DecryptionError)
    assert refused.key_id == missing.key_id == "key-a"
    assert str(refused) != str(missing)
