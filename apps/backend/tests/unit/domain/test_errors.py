"""Errors carry the facts callers branch on as attributes."""

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
    refused, missing = DecryptionError("key-a"), UnknownKeyError("key-a")
    assert isinstance(missing, DecryptionError)
    assert refused.key_id == missing.key_id == "key-a"
    assert str(refused) != str(missing)
