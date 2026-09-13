"""The cryptographic operations the domain depends on, without naming an algorithm."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from letmehandle.domain.models.auth import AuthenticatedUser
    from letmehandle.domain.models.identifiers import UserId


class SecretHasher(ABC):
    """Turns a one-time code or a refresh token into something safe to store."""

    @abstractmethod
    def hash(self, secret: str) -> str:
        """Hash a secret for storage."""

    @abstractmethod
    def verify(self, secret: str, hashed: str) -> bool:
        """Whether the secret matches, compared in constant time."""


class SecretGenerator(ABC):
    """Produces unpredictable values, unlike `IdGenerator`, which only needs them unique."""

    @abstractmethod
    def numeric_code(self, length: int) -> str:
        """A code of exactly this many digits, from a source fit for secrets."""

    @abstractmethod
    def token(self) -> str:
        """An unguessable opaque string."""


class TokenSigner(ABC):
    """Issues access tokens and verifies them by signature, without a lookup."""

    @abstractmethod
    def issue(self, user_id: UserId, issued_at: datetime) -> tuple[str, datetime]:
        """Sign a token for this user, returning it and when it expires."""

    @abstractmethod
    def verify(self, token: str) -> AuthenticatedUser:
        """Read a token, raising for one this service did not issue."""


@dataclass(frozen=True, slots=True)
class SealedBytes:
    """Ciphertext, and the id of the key that sealed it."""

    key_id: str
    ciphertext: bytes


class TranscriptCipher(ABC):
    """Seals transcripts and summaries for storage, bound to a context kept outside (D-014)."""

    @abstractmethod
    def seal(self, plaintext: bytes, context: Sequence[str]) -> SealedBytes:
        """Encrypt under the newest key. Two seals of the same bytes never look alike."""

    @abstractmethod
    def open(self, sealed: SealedBytes, context: Sequence[str]) -> bytes:
        """Decrypt for exactly this context, or raise `DecryptionError` or `UnknownKeyError`."""
