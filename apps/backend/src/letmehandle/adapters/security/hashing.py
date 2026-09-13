"""Hashing secrets for storage."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Final

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.ports.security import SecretGenerator, SecretHasher

# scrypt, from the standard library, because a dependency for this is a dependency that has to
# be trusted with every credential in the system. The parameters are the ones RFC 7914 gives as
# interactive: expensive enough that a leaked table is not a list of codes, cheap enough that
# signing in does not feel broken.
_COST: Final = 2**14
_BLOCK_SIZE: Final = 8
_PARALLELISM: Final = 1
_SALT_BYTES: Final = 16
_KEY_BYTES: Final = 32

# The fewest characters a server-held key may have.
MIN_KEY_LENGTH: Final = 32


class ScryptHasher(SecretHasher):
    """Salted scrypt, with the salt stored alongside the hash.

    Each secret gets its own salt, so two users with the same code produce different rows and a
    precomputed table is worth nothing.
    """

    def hash(self, secret: str) -> str:
        salt = secrets.token_bytes(_SALT_BYTES)
        derived = self._derive(secret, salt)
        return f"scrypt${salt.hex()}${derived.hex()}"

    def verify(self, secret: str, hashed: str) -> bool:
        try:
            algorithm, salt_hex, expected_hex = hashed.split("$")
        except ValueError:
            return False
        if algorithm != "scrypt":
            return False
        try:
            salt = bytes.fromhex(salt_hex)
            expected = bytes.fromhex(expected_hex)
        except ValueError:
            return False
        # compare_digest, not ==. A comparison that returns on the first wrong byte tells an
        # attacker how much of the secret they have right.
        return hmac.compare_digest(self._derive(secret, salt), expected)

    def _derive(self, secret: str, salt: bytes) -> bytes:
        return hashlib.scrypt(
            secret.encode(),
            salt=salt,
            n=_COST,
            r=_BLOCK_SIZE,
            p=_PARALLELISM,
            dklen=_KEY_BYTES,
        )


class DeterministicHasher(SecretHasher):
    """A keyed hash, for values that have to be looked up rather than checked.

    A refresh token is found by its hash, which a salted hash makes impossible: every row would
    have to be derived and compared. This uses HMAC with a server-held key instead — so the
    stored value is still useless without the key, but identical inputs still produce identical
    output and the lookup is an index rather than a scan.

    Not for one-time codes. A six-digit space is small enough that an attacker holding both the
    key and the table could enumerate it.
    """

    def __init__(self, key: str) -> None:
        if len(key) < MIN_KEY_LENGTH:
            raise InvariantError(
                f"the hashing key must be at least {MIN_KEY_LENGTH} characters; a short one is "
                "the weakest part of everything built on it"
            )
        self._key = key.encode()

    def hash(self, secret: str) -> str:
        return hmac.new(self._key, secret.encode(), hashlib.sha256).hexdigest()

    def verify(self, secret: str, hashed: str) -> bool:
        return hmac.compare_digest(self.hash(secret), hashed)


class SystemSecretGenerator(SecretGenerator):
    """Unguessable values from the operating system's own source."""

    def numeric_code(self, length: int) -> str:
        if length <= 0:
            raise InvariantError("a code of no digits is not a code")
        # randbelow per digit rather than a range: it avoids the modulo bias that makes some
        # codes marginally likelier than others, which is small and free to avoid.
        return "".join(str(secrets.randbelow(10)) for _ in range(length))

    def token(self) -> str:
        return secrets.token_urlsafe(32)
