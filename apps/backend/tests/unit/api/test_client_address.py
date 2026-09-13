"""Counting what one place asks for, behind a proxy and not."""

from __future__ import annotations

from ipaddress import ip_network

import pytest
from starlette.requests import Request

from letmehandle.api.client_address import client_source

PROXIES = (ip_network("10.0.0.0/8"),)


def request(peer: str | None, forwarded: str | None = None) -> Request:
    headers = [] if forwarded is None else [(b"x-forwarded-for", forwarded.encode())]
    scope = {
        "type": "http",
        "headers": headers,
        "client": None if peer is None else (peer, 443),
    }
    return Request(scope)


@pytest.mark.parametrize(
    ("peer", "forwarded", "expected"),
    [
        pytest.param("203.0.113.9", None, "203.0.113.9", id="a direct client is its own address"),
        pytest.param(
            "203.0.113.9",
            "198.51.100.1",
            "203.0.113.9",
            id="a header from somebody who is not our proxy is ignored",
        ),
        pytest.param(
            "10.0.0.5", "198.51.100.1", "198.51.100.1", id="our proxy says who it forwarded for"
        ),
        pytest.param(
            "10.0.0.5",
            "192.0.2.66, 198.51.100.1",
            "198.51.100.1",
            id="an address the client wrote in front of it is not believed",
        ),
        pytest.param(
            "10.0.0.5",
            "198.51.100.1, 10.1.2.3",
            "198.51.100.1",
            id="every trusted hop is passed over",
        ),
        pytest.param(
            "10.0.0.5",
            "198.51.100.1, garbage",
            "10.0.0.5",
            id="an unreadable hop ends the chain at the last address that was ours",
        ),
        pytest.param("10.0.0.5", None, "10.0.0.5", id="our proxy with no header is the proxy"),
        pytest.param(
            "10.0.0.5", "10.9.9.9", "10.9.9.9", id="a chain of nothing but our own proxies"
        ),
        pytest.param(
            "2001:db8:1:2:3:4:5:6",
            None,
            "2001:db8:1:2::/64",
            id="an IPv6 client is counted by its /64",
        ),
        pytest.param("testclient", None, "testclient", id="a peer that is not an address"),
    ],
)
def test_the_source_of_a_request(peer: str, forwarded: str | None, expected: str) -> None:
    assert client_source(request(peer, forwarded), PROXIES) == expected


def test_no_peer_is_no_source() -> None:
    assert client_source(request(None), PROXIES) is None
