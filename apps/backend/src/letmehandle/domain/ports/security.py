"""The cryptographic operations the domain depends on, without naming an algorithm.

Ports rather than direct calls, for two reasons. A test needs them to be fast, and a real
implementation needs them to be slow — a hash that takes a millisecond is a hash worth
attacking offline. And an algorithm that looks sound today is one to be able to replace without
touching the code that decides who is signed in.
"""

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
    """Turns a secret into something safe to store.

    Used for one-time codes and for refresh tokens. Neither is ever stored in the clear, so a
    database that leaks reveals nothing that can be presented back.
    """

    @abstractmethod
    def hash(self, secret: str) -> str:
        """Hash a secret for storage."""

    @abstractmethod
    def verify(self, secret: str, hashed: str) -> bool:
        """Whether the secret matches.

        Must compare in constant time. A comparison that returns early on the first wrong
        character tells an attacker how much of the code they have right.
        """


class SecretGenerator(ABC):
    """Produces unguessable values.

    Separate from `IdGenerator`: an identifier needs to be unique, and a secret needs to be
    unpredictable. Using a sequence for a code, or a counter for a token, is the kind of
    mistake that is invisible in review and total in effect.
    """

    @abstractmethod
    def numeric_code(self, length: int) -> str:
        """A code of exactly this many digits, from a source fit for secrets."""

    @abstractmethod
    def token(self) -> str:
        """An unguessable opaque string."""


class TokenSigner(ABC):
    """Issues and verifies access tokens.

    Stateless verification is the point: an access token is checked by signature rather than by
    a database lookup, which is what lets it be short-lived without making every request cost a
    query. Revocation therefore belongs to refresh tokens, which are stored.
    """

    @abstractmethod
    def issue(self, user_id: UserId, issued_at: datetime) -> tuple[str, datetime]:
        """Sign a token for this user, returning it and when it expires."""

    @abstractmethod
    def verify(self, token: str) -> AuthenticatedUser:
        """Read a token, or raise if it is not one this service issued.

        Raises rather than returning None. A caller that forgets to check a returned optional
        has written an authentication bypass, and the type system will not have stopped them.
        """


@dataclass(frozen=True, slots=True)
class SealedBytes:
    """Ciphertext, and the id of the key that sealed it.

    The key id is stored beside the ciphertext so that opening it is one lookup rather than a
    trial of every key, and so that a key an operator removed too early fails by name.
    """

    key_id: str
    ciphertext: bytes


class TranscriptCipher(ABC):
    """Encrypts what somebody said before it is stored, and opens it again (D-014).

    At the application layer rather than the disk's, so that a database dump is not a transcript
    dump. Every operation takes a context — whose record this is, which call, which entry — that
    is authenticated alongside the ciphertext but not stored inside it. Ciphertext copied onto
    another user's row, another call, or another speaker is refused rather than read as theirs.

    Summaries are sealed with it too: their evidence quotes the transcript word for word.
    """

    @abstractmethod
    def seal(self, plaintext: bytes, context: Sequence[str]) -> SealedBytes:
        """Encrypt under the newest key. Two seals of the same bytes never look alike."""

    @abstractmethod
    def open(self, sealed: SealedBytes, context: Sequence[str]) -> bytes:
        """Decrypt, or raise `DecryptionError` — `UnknownKeyError` for a key no longer held.

        Never returns bytes that were not sealed for exactly this context.
        """
