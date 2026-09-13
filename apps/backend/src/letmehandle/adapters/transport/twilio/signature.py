"""The provider's HMAC-SHA1 request signature, checked against the configured public URL."""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import TYPE_CHECKING, Final
from urllib.parse import parse_qsl, urlsplit, urlunsplit

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

SIGNATURE_HEADER: Final = "X-Twilio-Signature"

_DEFAULT_HTTPS_PORT: Final = 443


class SignatureRejectedError(Exception):
    """A request not shown to come from the provider; names the failed check, never the request."""


def compute_signature(url: str, params: Iterable[tuple[str, str]], auth_token: str) -> str:
    """The provider's signature for a URL and form parameters, each distinct pair once, sorted."""
    payload = url + "".join(name + value for name, value in sorted(set(params)))
    digest = hmac.new(auth_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1)
    return base64.b64encode(digest.digest()).decode("ascii")


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
    """The URL as configured, and without or with the default port when it is secure."""
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
