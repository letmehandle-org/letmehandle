"""Releasing whatever holds a connection, among providers that may or may not hold one."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Iterable


@runtime_checkable
class Closable(Protocol):
    async def aclose(self) -> None:
        """Release what it holds."""


async def close_each(providers: Iterable[object]) -> None:
    """Close each provider that holds something, once, however many times it is listed."""
    closed: list[object] = []
    for provider in providers:
        if isinstance(provider, Closable) and not any(provider is done for done in closed):
            closed.append(provider)
            await provider.aclose()
