"""The device token lifecycle: registration, rotation, and sign-out."""

from __future__ import annotations

import pytest

from letmehandle.application.escalation.devices import DeviceRegistrationService
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.identifiers import UserId
from letmehandle.domain.ports.notification import DevicePlatform, DeviceToken
from tests.support.escalation_stores import InMemoryDevices

ALICE = UserId("user-1")
BOB = UserId("user-2")
OLD = DeviceToken(DevicePlatform.IOS, "old-token")
NEW = DeviceToken(DevicePlatform.IOS, "new-token")


async def test_registering_twice_is_the_same_as_once() -> None:
    devices = InMemoryDevices()
    service = DeviceRegistrationService(devices)
    await service.register(ALICE, OLD)
    await service.register(ALICE, OLD)
    assert await devices.tokens_for(ALICE) == [OLD]


async def test_rotation_replaces_the_old_token() -> None:
    devices = InMemoryDevices()
    service = DeviceRegistrationService(devices)
    await service.register(ALICE, OLD)
    await service.register(ALICE, NEW, replacing=OLD)
    assert await devices.tokens_for(ALICE) == [NEW]


async def test_rotation_to_the_same_token_keeps_it() -> None:
    devices = InMemoryDevices()
    service = DeviceRegistrationService(devices)
    await service.register(ALICE, OLD, replacing=OLD)
    assert await devices.tokens_for(ALICE) == [OLD]


async def test_rotation_cannot_remove_another_user_s_token() -> None:
    devices = InMemoryDevices()
    service = DeviceRegistrationService(devices)
    await service.register(BOB, OLD)
    await service.register(ALICE, NEW, replacing=OLD)
    assert await devices.tokens_for(BOB) == [OLD]


async def test_rotation_across_platforms_is_refused() -> None:
    service = DeviceRegistrationService(InMemoryDevices())
    with pytest.raises(InvariantError, match="within its platform"):
        await service.register(ALICE, NEW, replacing=DeviceToken(DevicePlatform.ANDROID, "x"))


async def test_a_token_moves_to_whoever_registers_it_last() -> None:
    devices = InMemoryDevices()
    service = DeviceRegistrationService(devices)
    await service.register(ALICE, OLD)
    await service.register(BOB, OLD)
    assert await devices.tokens_for(ALICE) == []


async def test_removing_is_scoped_to_the_user_and_silent_when_absent() -> None:
    devices = InMemoryDevices()
    service = DeviceRegistrationService(devices)
    await service.register(BOB, OLD)
    await service.remove(ALICE, OLD)
    await service.remove(ALICE, NEW)
    assert await devices.tokens_for(BOB) == [OLD]
    await service.remove(BOB, OLD)
    assert await devices.tokens_for(BOB) == []
