"""What both push adapters need and neither owns."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any, Final

from cryptography.hazmat.primitives.serialization import load_pem_private_key

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes

    from letmehandle.domain.models.identifiers import CallId

# The largest collapse identifier APNs accepts. FCM documents no limit, and one rule for both
# means a call collapses the same way on either platform.
MAX_COLLAPSE_ID_BYTES: Final = 64


class CredentialError(ValueError):
    """A configured credential cannot be used. The message never repeats the credential."""


def collapse_id_for(call_id: CallId) -> str:
    """The identifier repeated notifications for one call are merged under.

    The call id itself when it fits, so a delivery can be matched to its call by eye; a digest of
    it when it does not, which is still one value per call and never a truncation that could
    merge two calls sharing a prefix.
    """
    raw = call_id.value
    if len(raw.encode()) <= MAX_COLLAPSE_ID_BYTES and raw.isascii():
        return raw
    return hashlib.sha256(raw.encode()).hexdigest()


def compact_json(document: dict[str, Any]) -> bytes:
    """JSON as it goes on the wire: no padding, and text as UTF-8 rather than escapes.

    Escaping every non-ASCII character as `\\uXXXX` would make a notification in another script
    up to three times its real size against a byte limit.
    """
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()


def load_private_key(pem: str, *, what: str) -> PrivateKeyTypes:
    """A PEM private key, accepting the escaped newlines a single-line environment variable has."""
    text = pem.replace("\\n", "\n").strip()
    try:
        return load_pem_private_key(text.encode(), password=None)
    except (ValueError, TypeError):
        # The cause is dropped as well as the text: a parser's message can quote the input.
        raise CredentialError(f"{what} is not a readable PEM private key") from None
