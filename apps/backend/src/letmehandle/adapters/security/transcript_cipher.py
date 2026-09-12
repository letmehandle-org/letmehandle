"""Transcript encryption: AES-256-GCM with named, rotatable keys.

Why AES-GCM from `cryptography` rather than Fernet or MultiFernet, which are the more obvious
choice from the same package:

  Associated data. Every sealed value is bound to the record it belongs to — user, call, speaker
  and moment — without that context being stored inside it. GCM authenticates associated data
  natively; Fernet has no such input, so binding a context would mean inventing a construction
  on top of it, which is exactly the kind of cryptography nobody should write.

  Named keys. MultiFernet rotates by trying each key in turn, so a key removed too early fails
  as "invalid token", indistinguishable from tampering. Here the key id is stored beside the
  ciphertext, so opening is one lookup and a missing key fails by name (`UnknownKeyError`) with
  the remedy in the message.

Rotation: keys are configured newest first. `seal` always uses the newest; `open` uses whichever
the stored id names. Introducing a key is therefore adding it at the front; retiring one is
removing it once no stored row names it. Transcripts age out within the retention ceiling, so
a transcript key can be retired that long after it stopped being newest. Summaries do not age
out, so a key that sealed a summary stays for as long as that summary does — see the purge's
module for what that means for an operator.

Layout of `ciphertext`: a fresh 96-bit nonce, then GCM's output (the encrypted bytes followed by
its 128-bit tag). A nonce is never reused under a key: it comes from the operating system's
random source for every seal, which is why two seals of the same text never look alike.
"""

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
    """The context, encoded without ambiguity.

    Each part is length-prefixed. Joining with a separator would let ("ab", "c") and ("a", "bc")
    authenticate as the same record, and identifiers are not guaranteed to exclude any byte.
    """
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
