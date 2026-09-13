"""What a handshake names in the endpoint URL."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from letmehandle.adapters.speech.websocket.endpoint import with_query_parameter


def test_the_parameter_is_added_and_the_rest_of_the_url_kept() -> None:
    url = with_query_parameter("wss://speech.example.com/v1/talk?region=a", "model", "m-1")
    parts = urlsplit(url)
    assert (parts.scheme, parts.netloc, parts.path) == ("wss", "speech.example.com", "/v1/talk")
    assert parse_qs(parts.query) == {"region": ["a"], "model": ["m-1"]}


def test_a_value_already_in_the_url_is_replaced_not_duplicated() -> None:
    # An example value already in the URL is replaced, not duplicated.
    url = with_query_parameter("wss://speech.example.com/talk?agent_id=example", "agent_id", "real")
    assert parse_qs(urlsplit(url).query) == {"agent_id": ["real"]}
