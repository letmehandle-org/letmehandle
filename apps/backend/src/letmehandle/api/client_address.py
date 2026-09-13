"""Who is asking, for counting what one place asks for.

The peer the connection came from, unless that peer is a proxy this deployment was told to trust,
in which case the address that proxy says it forwarded for. Nothing else in the header is believed:
`X-Forwarded-For` is written by whoever sends the request, and only the entries our own proxies
appended can be relied on. So the list is read from the right, past every trusted proxy, and the
first address that is not one of ours is the client.

An IPv6 client is counted by its /64, because a single subscriber is routinely handed a whole /64
and can pick a new address in it for every request.
"""

from __future__ import annotations

from ipaddress import IPv4Network, IPv6Address, IPv6Network, ip_address
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastapi import Request


def client_source(
    request: Request, trusted_proxies: Sequence[IPv4Network | IPv6Network]
) -> str | None:
    """A key for the client behind this request, or nothing when there is no peer at all."""
    peer = request.client.host if request.client else None
    if peer is None:
        return None
    address = _parsed(peer)
    if address is None:
        return peer

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and _is_trusted(address, trusted_proxies):
        for hop in reversed([part.strip() for part in forwarded.split(",") if part.strip()]):
            candidate = _parsed(hop)
            if candidate is None:
                # A hop nobody can read ends the chain: nothing to its left can be trusted either.
                break
            address = candidate
            if not _is_trusted(candidate, trusted_proxies):
                break

    if isinstance(address, IPv6Address):
        return str(IPv6Network(f"{address}/64", strict=False))
    return str(address)


def _parsed(text: str) -> object | None:
    try:
        return ip_address(text)
    except ValueError:
        return None


def _is_trusted(address: object, networks: Sequence[IPv4Network | IPv6Network]) -> bool:
    return any(address in network for network in networks)
