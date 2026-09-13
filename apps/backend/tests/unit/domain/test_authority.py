"""What the assistant is granted, closed by default."""

from __future__ import annotations

import pytest

from letmehandle.domain.models.authority import AgentAuthority, Capability


def test_nothing_is_granted_by_default() -> None:
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
    authority = AgentAuthority.none()
    with pytest.raises(AttributeError):
        authority.capabilities = frozenset(Capability)  # type: ignore[misc]


def test_two_authorities_granting_the_same_things_are_equal() -> None:
    assert AgentAuthority.granting(
        Capability.TAKE_A_MESSAGE, Capability.CONFIRM_APPOINTMENTS
    ) == AgentAuthority.granting(Capability.CONFIRM_APPOINTMENTS, Capability.TAKE_A_MESSAGE)
