"""The capability matrix in the architecture documentation is what the built transports declare."""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from letmehandle.bootstrap import build_call_transports, build_reported_calls
from letmehandle.config.settings import TelephonyProviderName
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.ports.call_transport import TransportCapabilities
from tests.support.config import make_settings
from tests.support.observability import recorded_observability

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

DOCUMENT = Path(__file__).resolve().parents[4] / "docs" / "architecture" / "call-transport.md"

_ROW = re.compile(r"^\|(.+)\|\s*$")
_CODE = re.compile(r"`([a-z_]+)`")


def documented_matrix() -> dict[str, dict[str, bool]]:
    """The table under "Capability matrix", as transport name to capability to declared."""
    text = DOCUMENT.read_text(encoding="utf-8")
    section = text.split("## Capability matrix", 1)[1].split("\n### ", 1)[0]
    rows = [
        [cell.strip() for cell in match.group(1).split("|")]
        for line in section.splitlines()
        if (match := _ROW.match(line))
    ]
    header, _divider, *body = rows
    transports = [code_in(cell) for cell in header[1:]]
    matrix: dict[str, dict[str, bool]] = {name: {} for name in transports}
    for capability_cell, *values in body:
        capability = code_in(capability_cell)
        for name, value in zip(transports, values, strict=True):
            assert value in {"yes", "no"}, f"{name}/{capability}: {value!r}"
            matrix[name][capability] = value == "yes"
    return matrix


def code_in(cell: str) -> str:
    match = _CODE.fullmatch(cell)
    assert match is not None, f"expected one name in backticks, got {cell!r}"
    return match.group(1)


@asynccontextmanager
async def declared(provider: TelephonyProviderName) -> AsyncIterator[TransportCapabilities]:
    """What the transport bootstrap builds for `provider` declares, released afterwards."""
    settings = make_settings(
        telephony_provider=provider,
        telephony_account_id="account-for-tests",
        telephony_auth_token="token-for-tests",
        telephony_numbers=(PhoneNumber.parse("+12025550100"),),
        telephony_app_id="app-for-tests",
        telephony_webhook_base_url="https://calls.example.com",
    )
    (binding,) = build_call_transports(
        settings, reported_calls=build_reported_calls(), observability=recorded_observability()
    )
    try:
        yield binding.transport.capabilities
    finally:
        await binding.close()


def test_the_matrix_has_a_column_for_every_transport_and_a_row_for_every_capability() -> None:
    matrix = documented_matrix()
    assert set(matrix) == {provider.value for provider in TelephonyProviderName}
    for rows in matrix.values():
        assert set(rows) == set(TransportCapabilities().names())


@pytest.mark.parametrize("provider", list(TelephonyProviderName))
async def test_each_column_is_what_that_transport_declares(
    provider: TelephonyProviderName,
) -> None:
    documented = documented_matrix()[provider.value]
    async with declared(provider) as capabilities:
        assert documented == {name: capabilities.has(name) for name in capabilities.names()}
