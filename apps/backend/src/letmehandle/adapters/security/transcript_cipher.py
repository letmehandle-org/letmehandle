"""Transcript encryption: AES-256-GCM with associated data and named keys, newest first."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from letmehandle.domain.errors import DecryptionError, InvariantError, UnknownKeyError
from letmehandle.domain.ports.security import SealedBytes, TranscriptCipher

if TYPE_CHECKING:
    from collections.abc import Sequence

KEY_BYTES: Final = 32
NONCE_BYTES: Final = 12
TAG_BYTES: Final = 16

# Versioned, so a later change to how the context is encoded cannot open what this one sealed.
_DOMAIN_SEPARATOR: Final = b"letmehandle.sealed.v1"


def _associated_data(context: Sequence[str]) -> bytes:
    """The context, each part length-prefixed so different splits never authenticate alike."""
    encoded = bytearray(_DOMAIN_SEPARATOR)
    for part in context:
        raw = part.encode()
        encoded += len(raw).to_bytes(4, "big") + raw
    return bytes(encoded)


class AesGcmTranscriptCipher(TranscriptCipher):
    """Seals under the newest key, opens under whichever key the stored id names."""

    def __init__(self, keys: Sequence[tuple[str, bytes]]) -> None:
        if not keys:
            raise InvariantError("a cipher needs at least one key")
        ids = [key_id for key_id, _ in keys]
        if len(set(ids)) != len(ids):
            raise InvariantError("two keys share an id, so a stored id could name either")
        if any(len(key) != KEY_BYTES for _, key in keys):
            raise InvariantError(f"every key is exactly {KEY_BYTES} bytes")
        self._newest_id = ids[0]
        self._keys = {key_id: AESGCM(key) for key_id, key in keys}

    def __repr__(self) -> str:
        # The ids only. A repr is what a debugger, a log line or a failing assertion prints.
        return f"AesGcmTranscriptCipher(key_ids={list(self._keys)!r})"

    def seal(self, plaintext: bytes, context: Sequence[str]) -> SealedBytes:
        nonce = secrets.token_bytes(NONCE_BYTES)
        encrypted = self._keys[self._newest_id].encrypt(nonce, plaintext, _associated_data(context))
        return SealedBytes(key_id=self._newest_id, ciphertext=nonce + encrypted)

    def open(self, sealed: SealedBytes, context: Sequence[str]) -> bytes:
        key = self._keys.get(sealed.key_id)
        if key is None:
            raise UnknownKeyError(sealed.key_id)
        if len(sealed.ciphertext) < NONCE_BYTES + TAG_BYTES:
            raise DecryptionError(sealed.key_id)
        nonce, encrypted = sealed.ciphertext[:NONCE_BYTES], sealed.ciphertext[NONCE_BYTES:]
        try:
            return key.decrypt(nonce, encrypted, _associated_data(context))
        except InvalidTag as error:
            # InvalidTag carries no message, so chaining it discloses nothing.
            raise DecryptionError(sealed.key_id) from error
