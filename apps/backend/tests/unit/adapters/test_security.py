"""The cryptography everything else rests on."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from letmehandle.adapters.security.hashing import (
    DeterministicHasher,
    ScryptHasher,
    SystemSecretGenerator,
)
from letmehandle.adapters.security.tokens import InvalidTokenError, JWTTokenSigner
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.identifiers import UserId
from tests.contracts.fakes import FixedClock

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
A_KEY = "a" * 48
USER = UserId("user-1")


class TestScryptHasher:
    def test_a_secret_verifies_against_its_own_hash(self) -> None:
        hasher = ScryptHasher()
        assert hasher.verify("424242", hasher.hash("424242"))

    def test_a_different_secret_does_not(self) -> None:
        hasher = ScryptHasher()
        assert not hasher.verify("000000", hasher.hash("424242"))

    def test_the_same_secret_hashes_differently_every_time(self) -> None:
        # Each secret gets its own salt, so two people with the same code produce different
        # rows and a precomputed table is worth nothing.
        hasher = ScryptHasher()
        assert hasher.hash("424242") != hasher.hash("424242")

    def test_the_secret_does_not_appear_in_the_hash(self) -> None:
        assert "424242" not in ScryptHasher().hash("424242")

    @pytest.mark.parametrize(
        "stored",
        ["", "nonsense", "scrypt$notrelevant", "bcrypt$aa$bb", "scrypt$zz$zz", "a$b$c$d"],
    )
    def test_a_malformed_stored_value_fails_rather_than_raises(self, stored: str) -> None:
        # A corrupted row must refuse the sign-in, not take the process down with it.
        assert not ScryptHasher().verify("424242", stored)


class TestDeterministicHasher:
    def test_the_same_input_always_gives_the_same_output(self) -> None:
        # The property a lookup needs: a refresh token is found by its hash, and a salted hash
        # would turn that index into a scan of every row.
        hasher = DeterministicHasher(A_KEY)
        assert hasher.hash("a-token") == hasher.hash("a-token")

    def test_a_different_key_gives_a_different_output(self) -> None:
        # So the stored value is useless to anybody without the key.
        assert DeterministicHasher(A_KEY).hash("t") != DeterministicHasher("b" * 48).hash("t")

    def test_it_verifies_what_it_hashed(self) -> None:
        hasher = DeterministicHasher(A_KEY)
        assert hasher.verify("a-token", hasher.hash("a-token"))
        assert not hasher.verify("another-token", hasher.hash("a-token"))

    def test_a_short_key_is_refused(self) -> None:
        with pytest.raises(InvariantError, match="32 characters"):
            DeterministicHasher("too-short")


class TestSecretGenerator:
    def test_a_code_has_exactly_the_requested_digits(self) -> None:
        code = SystemSecretGenerator().numeric_code(6)
        assert len(code) == 6
        assert code.isdigit()

    def test_codes_differ(self) -> None:
        generated = {SystemSecretGenerator().numeric_code(6) for _ in range(50)}
        assert len(generated) > 40

    def test_a_code_of_no_digits_is_refused(self) -> None:
        with pytest.raises(InvariantError):
            SystemSecretGenerator().numeric_code(0)

    def test_tokens_are_long_and_unique(self) -> None:
        generator = SystemSecretGenerator()
        tokens = {generator.token() for _ in range(50)}
        assert len(tokens) == 50
        assert all(len(token) >= 32 for token in tokens)


class TestJWTTokenSigner:
    def signer(self, lifetime: timedelta = timedelta(minutes=15)) -> JWTTokenSigner:
        return JWTTokenSigner(signing_key=A_KEY, lifetime=lifetime, clock=FixedClock(NOW))

    def test_a_token_it_issued_verifies_to_the_same_user(self) -> None:
        token, expires_at = self.signer().issue(USER, NOW)
        verified = self.signer().verify(token)
        assert verified.user_id == USER
        assert verified.expires_at == expires_at

    def test_a_tampered_token_is_refused(self) -> None:
        token, _ = self.signer().issue(USER, NOW)
        tampered = token[:-4] + ("aaaa" if not token.endswith("aaaa") else "bbbb")
        with pytest.raises(InvalidTokenError):
            self.signer().verify(tampered)

    def test_a_token_signed_with_another_key_is_refused(self) -> None:
        other = JWTTokenSigner(
            signing_key="b" * 48, lifetime=timedelta(minutes=15), clock=FixedClock(NOW)
        )
        token, _ = other.issue(USER, NOW)
        with pytest.raises(InvalidTokenError):
            self.signer().verify(token)

    def test_an_expired_token_is_refused(self) -> None:
        token, _ = self.signer(timedelta(seconds=1)).issue(USER, NOW - timedelta(hours=1))
        with pytest.raises(InvalidTokenError):
            self.signer().verify(token)

    def test_expiry_is_judged_by_the_clock_the_application_was_given(self) -> None:
        # Not the machine's. The library checks against the system clock by default, which
        # means one part of the system disagrees with every other about what time it is — and
        # makes this behaviour impossible to test without waiting.
        clock = FixedClock(NOW)
        signer = JWTTokenSigner(signing_key=A_KEY, lifetime=timedelta(minutes=15), clock=clock)
        token, _ = signer.issue(USER, NOW)

        assert signer.verify(token).user_id == USER

        clock.advance(timedelta(minutes=16).total_seconds())
        with pytest.raises(InvalidTokenError):
            signer.verify(token)

    def test_a_token_issued_in_the_future_is_refused(self) -> None:
        # A clock problem or a forgery, and either way not something to accept.
        clock = FixedClock(NOW)
        signer = JWTTokenSigner(signing_key=A_KEY, lifetime=timedelta(minutes=15), clock=clock)
        token, _ = signer.issue(USER, NOW + timedelta(hours=1))
        with pytest.raises(InvalidTokenError):
            signer.verify(token)

    def test_an_unsigned_token_is_refused(self) -> None:
        # The algorithm-confusion attack: a token whose header asks to be trusted without a
        # signature. Accepting the header's choice of algorithm is a one-word mistake.
        forged = jwt.encode(
            {
                "sub": USER.value,
                "iat": int(NOW.timestamp()),
                "exp": int((NOW + timedelta(hours=1)).timestamp()),
                "iss": "letmehandle",
                "aud": "letmehandle-api",
            },
            key="",
            algorithm="none",
        )
        with pytest.raises(InvalidTokenError):
            self.signer().verify(forged)

    def test_a_token_for_another_audience_is_refused(self) -> None:
        # Same key, different purpose. Without the audience check, a token minted for one thing
        # is accepted for another.
        foreign = jwt.encode(
            {
                "sub": USER.value,
                "iat": int(NOW.timestamp()),
                "exp": int((NOW + timedelta(hours=1)).timestamp()),
                "iss": "letmehandle",
                "aud": "somewhere-else",
            },
            key=A_KEY,
            algorithm="HS256",
        )
        with pytest.raises(InvalidTokenError):
            self.signer().verify(foreign)

    def test_a_token_missing_a_required_claim_is_refused(self) -> None:
        incomplete = jwt.encode({"sub": USER.value}, key=A_KEY, algorithm="HS256")
        with pytest.raises(InvalidTokenError):
            self.signer().verify(incomplete)

    def test_nonsense_is_refused(self) -> None:
        with pytest.raises(InvalidTokenError):
            self.signer().verify("not-a-token")

    def test_a_short_signing_key_is_refused(self) -> None:
        with pytest.raises(InvariantError, match="32 characters"):
            JWTTokenSigner(
                signing_key="short", lifetime=timedelta(minutes=15), clock=FixedClock(NOW)
            )

    def test_a_lifetime_of_nothing_is_refused(self) -> None:
        with pytest.raises(InvariantError):
            JWTTokenSigner(signing_key=A_KEY, lifetime=timedelta(0), clock=FixedClock(NOW))
