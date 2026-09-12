"""Naming what to talk to in the endpoint URL, which is where websocket services put it."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def with_query_parameter(endpoint_url: str, name: str, value: str) -> str:
    """The endpoint with `name` set to `value`, replacing any value already there.

    Replacing rather than appending, because a URL copied from a service's own documentation
    often carries an example value, and a service handed two is free to pick the wrong one.
    """
    parts = urlsplit(endpoint_url)
    query = [(key, each) for key, each in parse_qsl(parts.query) if key != name]
    query.append((name, value))
    return urlunsplit(parts._replace(query=urlencode(query)))
