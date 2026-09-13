"""Authority is checked in code. A prompt is guidance; an unknown caller is talking to it too."""

from __future__ import annotations

import pytest

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


def test_authority_cannot_widen_itself_in_place() -> None:
    # Frozen so that a check made earlier in a call cannot be invalidated by something that
    # happened later in it.
    authority = AgentAuthority.none()
    with pytest.raises(AttributeError):
        authority.capabilities = frozenset(Capability)  # type: ignore[misc]


def test_two_authorities_granting_the_same_things_are_equal() -> None:
    # Equality by value, so that a stored authority and a rebuilt one compare the same.
    assert AgentAuthority.granting(
        Capability.TAKE_A_MESSAGE, Capability.CONFIRM_APPOINTMENTS
    ) == AgentAuthority.granting(Capability.CONFIRM_APPOINTMENTS, Capability.TAKE_A_MESSAGE)
