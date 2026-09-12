"""Authority is checked in code. A prompt is guidance; an unknown caller is talking to it too."""

from __future__ import annotations

import pytest

from letmehandle.domain.errors import NotAuthorisedError
from letmehandle.domain.models.authority import AgentAuthority, Capability


def test_nothing_is_granted_by_default() -> None:
    # The direction the default has to fail in. A forgotten grant produces an assistant that
    # escalates too readily; a forgotten revocation produces one that agreed to something on a
    # stranger's say-so.
    authority = AgentAuthority.none()
    for capability in Capability:
        assert not authority.allows(capability)
    assert not authority


def test_granting_permits_exactly_what_was_granted() -> None:
    authority = AgentAuthority.granting(Capability.TAKE_A_MESSAGE)
    assert authority.allows(Capability.TAKE_A_MESSAGE)
    assert not authority.allows(Capability.CONFIRM_APPOINTMENTS)
    assert authority


def test_requiring_a_granted_capability_is_silent() -> None:
    AgentAuthority.granting(Capability.TAKE_A_MESSAGE).require(Capability.TAKE_A_MESSAGE)


def test_requiring_an_ungranted_capability_refuses_and_names_the_action() -> None:
    with pytest.raises(NotAuthorisedError) as failure:
        AgentAuthority.none().require(Capability.CONFIRM_APPOINTMENTS)
    assert failure.value.action == "confirm appointments"


def test_authority_cannot_widen_itself_in_place() -> None:
    # Frozen so that a check made earlier in a call cannot be invalidated by something that
    # happened later in it.
    authority = AgentAuthority.none()
    with pytest.raises(AttributeError):
        authority.capabilities = frozenset(Capability)  # type: ignore[misc]


def test_granting_and_revoking_produce_new_sets() -> None:
    original = AgentAuthority.none()
    granted = original.with_granted(Capability.TAKE_A_MESSAGE)
    revoked = granted.with_revoked(Capability.TAKE_A_MESSAGE)

    assert not original.allows(Capability.TAKE_A_MESSAGE)
    assert granted.allows(Capability.TAKE_A_MESSAGE)
    assert not revoked.allows(Capability.TAKE_A_MESSAGE)


def test_revoking_something_never_granted_changes_nothing() -> None:
    assert AgentAuthority.none().with_revoked(Capability.TAKE_A_MESSAGE) == AgentAuthority.none()


def test_two_authorities_granting_the_same_things_are_equal() -> None:
    # Equality by value, so that a stored authority and a rebuilt one compare the same.
    assert AgentAuthority.granting(
        Capability.TAKE_A_MESSAGE, Capability.CONFIRM_APPOINTMENTS
    ) == AgentAuthority.granting(Capability.CONFIRM_APPOINTMENTS, Capability.TAKE_A_MESSAGE)
