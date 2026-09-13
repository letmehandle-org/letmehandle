"""Proving a request came from the telephony provider, before anything in it is believed.

The provider signs every request it makes: the URL it called, from the scheme to the end of the
query string, followed by every form parameter sorted by name with each name and value written
out with no delimiters, signed with HMAC-SHA1 under the account's auth token and base64-encoded
into a header. A JSON body is not itemised; its SHA-256 goes into the URL as `bodySHA256` and
is signed as part of it.

Implemented here rather than taken from the vendor SDK, which would bring a second and third
HTTP stack to compute one HMAC. What the documentation warns about is written down as code, and
each warning has a test:

- The URL is the one the provider was configured to call, which behind a proxy or a tunnel is
  not the Host a request arrives with. It is always built from the configured base URL.
- Over https the provider drops the port before signing; over http it keeps it. The official
  validators accept a URL either way, and so does this.
- A fragment is never sent and never signed.
- Every parameter received is signed, including ones this code has never heard of. Parameters
  are never filtered to a known list, because the provider adds them without notice.
- A websocket handshake may have been signed with a trailing slash on the URL.
- A repeated parameter is signed over its distinct values in sorted order, so the order its
  values arrive in is not signed. What reads a callback refuses one repeating a parameter it
  reads, rather than letting that order decide which value is meant.

Nothing here reads a parameter's meaning. The verified parameters are handed back and only then
does anything look inside them.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import TYPE_CHECKING, Final
from urllib.parse import parse_qsl, urlsplit, urlunsplit

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

SIGNATURE_HEADER: Final = "X-Twilio-Signature"
BODY_HASH_PARAMETER: Final = "bodySHA256"

_DEFAULT_HTTPS_PORT: Final = 443


class SignatureRejectedError(Exception):
    """A request that cannot be shown to come from the provider.

    Adapter-internal, and deliberately vague: the reason says which check failed, never what was
    received, because what was received may be somebody's phone number or an attacker's probe.
    """


def compute_signature(url: str, params: Iterable[tuple[str, str]], auth_token: str) -> str:
    """The signature the provider would send for this URL and these form parameters.

    A parameter carrying the same value twice is written out once, as the official validators
    write it: they sort the set of each parameter's values.
    """
    payload = url + "".join(name + value for name, value in sorted(set(params)))
    digest = hmac.new(auth_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1)
    return base64.b64encode(digest.digest()).decode("ascii")


def body_hash(body: bytes) -> str:
    """What the provider puts in `bodySHA256` for a JSON body."""
    return hashlib.sha256(body).hexdigest()


class SignatureVerifier:
    """Checks requests against one account's token and one public base URL."""

    def __init__(self, *, auth_token: str, public_base_url: str) -> None:
        self._token = auth_token
        self._base = public_base_url.rstrip("/")

    def url_for(self, path: str, raw_query: str = "") -> str:
        """The URL the provider called, rebuilt from configuration and the request's path."""
        url = self._base + path
        return f"{url}?{raw_query}" if raw_query else url

    def verify_form(
        self, *, path: str, raw_query: str, body: bytes, signature: str | None
    ) -> list[tuple[str, str]]:
        """The request's form parameters, once the signature over them is proved."""
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            raise SignatureRejectedError("the body is not text a signature could cover") from None
        params = parse_qsl(text, keep_blank_values=True)
        self._verify(self.url_for(path, raw_query), params, signature)
        return params

    def verify_json(self, *, path: str, raw_query: str, body: bytes, signature: str | None) -> None:
        """Prove a JSON request: its body hash matches, and the URL carrying the hash is signed."""
        claimed = [
            value
            for name, value in parse_qsl(raw_query, keep_blank_values=True)
            if name == BODY_HASH_PARAMETER
        ]
        if len(claimed) != 1 or not hmac.compare_digest(claimed[0], body_hash(body)):
            raise SignatureRejectedError("the body does not match the hash that was signed")
        self._verify(self.url_for(path, raw_query), (), signature)

    def verify_handshake(self, *, path: str, raw_query: str, signature: str | None) -> None:
        """Prove a websocket handshake, which carries no body and may be signed with a slash."""
        url = self._websocket_url(self.url_for(path, raw_query))
        parts = urlsplit(url)
        slashed = urlunsplit(parts._replace(path=parts.path.rstrip("/") + "/"))
        unslashed = urlunsplit(parts._replace(path=parts.path.rstrip("/")))
        self._verify_any((url, slashed, unslashed), (), signature)

    def websocket_url(self, path: str) -> str:
        """The websocket URL the provider is told to connect to, for this path."""
        return self._websocket_url(self.url_for(path))

    def _verify(self, url: str, params: Sequence[tuple[str, str]], signature: str | None) -> None:
        self._verify_any((url,), params, signature)

    def _verify_any(
        self, urls: Iterable[str], params: Sequence[tuple[str, str]], signature: str | None
    ) -> None:
        if not signature:
            raise SignatureRejectedError("the request is not signed")
        for url in urls:
            for candidate in _port_variants(url):
                expected = compute_signature(candidate, params, self._token)
                if hmac.compare_digest(expected, signature):
                    return
        raise SignatureRejectedError("the signature does not match")

    @staticmethod
    def _websocket_url(url: str) -> str:
        parts = urlsplit(url)
        scheme = {"https": "wss", "http": "ws"}.get(parts.scheme, parts.scheme)
        return urlunsplit(parts._replace(scheme=scheme))


def _port_variants(url: str) -> tuple[str, ...]:
    """The URL as configured, and as the provider may have signed it.

    A secure URL is signed without its port. The configured URL may carry one — a tunnel on a
    non-default port — or may not while the provider's own validator adds the default, so both
    forms are tried. A plain http URL is signed with its port and has one form only.
    """
    parts = urlsplit(url)._replace(fragment="")
    as_given = urlunsplit(parts)
    if parts.scheme not in {"https", "wss"}:
        return (as_given,)
    # Credentials are dropped along with the port, as the provider drops them.
    host = parts.hostname or ""
    host = f"[{host}]" if ":" in host else host
    without_port = urlunsplit(parts._replace(netloc=host))
    with_default = urlunsplit(parts._replace(netloc=f"{host}:{_DEFAULT_HTTPS_PORT}"))
    return tuple(dict.fromkeys((as_given, without_port, with_default)))
