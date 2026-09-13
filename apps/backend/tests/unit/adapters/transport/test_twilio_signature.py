"""Signature verification, including every pitfall the provider's documentation warns about.

The known-answer test pins the algorithm to an independent computation written out in full in
the test, so the implementation cannot pass by agreeing with itself.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest

from letmehandle.adapters.transport.twilio.signature import (
    SignatureRejectedError,
    SignatureVerifier,
    compute_signature,
)

TOKEN = "auth-token-for-tests"
BASE = "https://calls.example.com"
PATH = "/telephony/conference/status"
PARAMS = [
    ("CallSid", "CAsim-1"),
    ("AccountSid", "account-for-tests"),
    ("From", "+12025550123"),
    ("Caller", "+12025550123"),
]


def verifier(base: str = BASE) -> SignatureVerifier:
    return SignatureVerifier(auth_token=TOKEN, public_base_url=base)


def form(params: list[tuple[str, str]]) -> bytes:
    from urllib.parse import urlencode

    return urlencode(params).encode()


def test_the_signature_is_hmac_sha1_over_the_url_and_the_sorted_parameters() -> None:
    url = f"{BASE}{PATH}?call=CAsim-1"
    written_out = (
        url
        + "AccountSid"
        + "account-for-tests"
        + "CallSid"
        + "CAsim-1"
        + "Caller"
        + "+12025550123"
        + "From"
        + "+12025550123"
    )
    expected = base64.b64encode(
        hmac.new(TOKEN.encode(), written_out.encode(), hashlib.sha1).digest()
    ).decode()
    assert compute_signature(url, PARAMS, TOKEN) == expected


def test_sorting_is_case_sensitive_as_unix_sorting_is() -> None:
    # Upper case sorts before lower case: "Zed" comes before "apple".
    params = [("apple", "1"), ("Zed", "2")]
    assert compute_signature("u", params, TOKEN) == compute_signature(
        "u", [("Zed", "2"), ("apple", "1")], TOKEN
    )
    written_out = "uZed2apple1"
    assert (
        compute_signature("u", params, TOKEN)
        == base64.b64encode(
            hmac.new(TOKEN.encode(), written_out.encode(), hashlib.sha1).digest()
        ).decode()
    )


def test_a_repeated_parameter_is_signed_once_per_distinct_value_in_value_order() -> None:
    # The official validators sort the set of each parameter's values, so a value repeated
    # verbatim is written out once, and two values of one name are written in sorted order.
    written_out = "u" + "Event" + "join" + "Event" + "leave"
    expected = base64.b64encode(
        hmac.new(TOKEN.encode(), written_out.encode(), hashlib.sha1).digest()
    ).decode()
    repeated = [("Event", "leave"), ("Event", "join"), ("Event", "leave")]
    assert compute_signature("u", repeated, TOKEN) == expected


def test_a_genuine_form_callback_is_accepted_and_its_parameters_returned() -> None:
    url = f"{BASE}{PATH}?call=CAsim-1"
    params = verifier().verify_form(
        path=PATH,
        raw_query="call=CAsim-1",
        body=form(PARAMS),
        signature=compute_signature(url, PARAMS, TOKEN),
    )
    assert params == PARAMS


def test_a_tampered_payload_is_rejected() -> None:
    url = f"{BASE}{PATH}"
    signature = compute_signature(url, PARAMS, TOKEN)
    tampered = [(name, "+12025550199" if name == "From" else value) for name, value in PARAMS]
    with pytest.raises(SignatureRejectedError, match="does not match"):
        verifier().verify_form(path=PATH, raw_query="", body=form(tampered), signature=signature)


def test_a_tampered_query_is_rejected() -> None:
    signature = compute_signature(f"{BASE}{PATH}?call=CAsim-1", PARAMS, TOKEN)
    with pytest.raises(SignatureRejectedError):
        verifier().verify_form(
            path=PATH, raw_query="call=CAsim-2", body=form(PARAMS), signature=signature
        )


def test_a_signature_under_another_token_is_rejected() -> None:
    signature = compute_signature(f"{BASE}{PATH}", PARAMS, "somebody-elses-token")
    with pytest.raises(SignatureRejectedError):
        verifier().verify_form(path=PATH, raw_query="", body=form(PARAMS), signature=signature)


@pytest.mark.parametrize("signature", [None, ""])
def test_an_unsigned_request_is_rejected(signature: str | None) -> None:
    with pytest.raises(SignatureRejectedError, match="not signed"):
        verifier().verify_form(path=PATH, raw_query="", body=form(PARAMS), signature=signature)


def test_a_body_that_is_not_text_is_rejected_before_anything_reads_it() -> None:
    with pytest.raises(SignatureRejectedError, match="not text"):
        verifier().verify_form(path=PATH, raw_query="", body=b"\xff\xfe", signature="x")


def test_parameters_this_code_has_never_heard_of_are_still_signed_and_still_validate() -> None:
    # The provider adds parameters without notice. Filtering to a known list would reject every
    # callback the day one appears.
    extended = [*PARAMS, ("StirVerstat", "TN-Validation-Passed-A"), ("NewThing", "")]
    signature = compute_signature(f"{BASE}{PATH}", extended, TOKEN)
    assert (
        verifier().verify_form(path=PATH, raw_query="", body=form(extended), signature=signature)
        == extended
    )


def test_the_configured_url_is_what_is_checked_not_the_request_host() -> None:
    # Behind a tunnel the request arrives at a loopback address; the provider signed the public
    # URL. A verifier built from the request would reject every genuine callback.
    signature = compute_signature(f"{BASE}{PATH}", PARAMS, TOKEN)
    assert verifier().verify_form(path=PATH, raw_query="", body=form(PARAMS), signature=signature)
    loopback = compute_signature(f"http://127.0.0.1:8000{PATH}", PARAMS, TOKEN)
    with pytest.raises(SignatureRejectedError):
        verifier().verify_form(path=PATH, raw_query="", body=form(PARAMS), signature=loopback)


def test_https_drops_the_port_before_signing() -> None:
    # Configured with the tunnel's port; the provider signed without it.
    signature = compute_signature(f"https://calls.example.com{PATH}", PARAMS, TOKEN)
    verifier("https://calls.example.com:8443").verify_form(
        path=PATH, raw_query="", body=form(PARAMS), signature=signature
    )


def test_a_url_signed_with_the_default_https_port_also_validates() -> None:
    signature = compute_signature(f"https://calls.example.com:443{PATH}", PARAMS, TOKEN)
    verifier().verify_form(path=PATH, raw_query="", body=form(PARAMS), signature=signature)


def test_http_keeps_the_port() -> None:
    with_port = compute_signature(f"http://calls.example.com:8080{PATH}", PARAMS, TOKEN)
    verifier("http://calls.example.com:8080").verify_form(
        path=PATH, raw_query="", body=form(PARAMS), signature=with_port
    )
    without_port = compute_signature(f"http://calls.example.com{PATH}", PARAMS, TOKEN)
    with pytest.raises(SignatureRejectedError):
        verifier("http://calls.example.com:8080").verify_form(
            path=PATH, raw_query="", body=form(PARAMS), signature=without_port
        )


def test_credentials_in_the_configured_url_are_dropped_as_the_provider_drops_them() -> None:
    signature = compute_signature(f"https://calls.example.invalid{PATH}", PARAMS, TOKEN)
    verifier("https://user:secret@calls.example.invalid").verify_form(
        path=PATH, raw_query="", body=form(PARAMS), signature=signature
    )


def test_an_ipv6_host_keeps_its_brackets() -> None:
    signature = compute_signature(f"https://[::1]{PATH}", PARAMS, TOKEN)
    verifier("https://[::1]:8443").verify_form(
        path=PATH, raw_query="", body=form(PARAMS), signature=signature
    )


def test_a_fragment_is_never_part_of_what_was_signed() -> None:
    signature = compute_signature(f"{BASE}{PATH}", PARAMS, TOKEN)
    verifier().verify_form(
        path=PATH + "#section", raw_query="", body=form(PARAMS), signature=signature
    )


@pytest.mark.parametrize(
    "signed",
    ["wss://calls.example.com/telephony/media", "wss://calls.example.com/telephony/media/"],
)
def test_a_websocket_handshake_validates_with_or_without_a_trailing_slash(signed: str) -> None:
    signature = compute_signature(signed, [], TOKEN)
    verifier().verify_handshake(path="/telephony/media", raw_query="", signature=signature)


def test_a_websocket_handshake_arriving_with_a_slash_validates_against_one_signed_without() -> None:
    signature = compute_signature("wss://calls.example.com/telephony/media", [], TOKEN)
    verifier().verify_handshake(path="/telephony/media/", raw_query="", signature=signature)


def test_a_forged_websocket_handshake_is_rejected() -> None:
    signature = compute_signature("wss://calls.example.com/telephony/other", [], TOKEN)
    with pytest.raises(SignatureRejectedError):
        verifier().verify_handshake(path="/telephony/media", raw_query="", signature=signature)


def test_the_stream_url_uses_the_websocket_scheme_for_the_configured_one() -> None:
    assert verifier().websocket_url("/telephony/media") == "wss://calls.example.com/telephony/media"
    assert (
        verifier("http://calls.example.com:8080").websocket_url("/m")
        == "ws://calls.example.com:8080/m"
    )


def test_a_trailing_slash_on_the_base_url_does_not_double_up() -> None:
    assert verifier(BASE + "/").url_for(PATH, "a=b") == f"{BASE}{PATH}?a=b"
