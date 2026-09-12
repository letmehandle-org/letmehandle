"""Signing in has to be hard to forge and impossible to replay."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.auth import (
    CHALLENGE_LIFETIME,
    MAX_ATTEMPTS,
    AuthenticatedUser,
    ChallengeState,
    OTPChallenge,
    RefreshToken,
    TokenPair,
)
from letmehandle.domain.models.identifiers import UserId
from letmehandle.domain.models.phone_number import PhoneNumber

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
NUMBER = PhoneNumber.parse("+12025550143")


def a_challenge(**overrides: object) -> OTPChallenge:
    fields: dict[str, object] = {
        "id": "challenge-1",
        "phone_number": NUMBER,
        "code_hash": "a-hash",
        "issued_at": NOW,
        "expires_at": NOW + CHALLENGE_LIFETIME,
    }
    fields.update(overrides)
    return OTPChallenge(**fields)  # type: ignore[arg-type]


def a_token(**overrides: object) -> RefreshToken:
    fields: dict[str, object] = {
        "id": "token-1",
        "family_id": "family-1",
        "user_id": UserId("user-1"),
        "token_hash": "a-hash",
        "issued_at": NOW,
        "expires_at": NOW + timedelta(days=30),
    }
    fields.update(overrides)
    return RefreshToken(**fields)  # type: ignore[arg-type]


class TestChallenges:
    def test_a_fresh_challenge_is_open(self) -> None:
        assert a_challenge().is_open_at(NOW)
        assert a_challenge().attempts_remaining == MAX_ATTEMPTS

    def test_it_closes_when_it_expires(self) -> None:
        challenge = a_challenge()
        assert challenge.state_at(NOW + CHALLENGE_LIFETIME) is ChallengeState.EXPIRED
        assert not challenge.is_open_at(NOW + CHALLENGE_LIFETIME)

    def test_it_closes_when_the_attempts_run_out(self) -> None:
        # The strength is here, not in the length of the code: a million combinations is
        # nothing to a machine and everything to one with five tries.
        challenge = a_challenge()
        for _ in range(MAX_ATTEMPTS):
            challenge = challenge.with_failed_attempt()
        assert challenge.state_at(NOW) is ChallengeState.EXHAUSTED
        assert challenge.attempts_remaining == 0

    def test_using_it_marks_it_verified(self) -> None:
        assert a_challenge().verified(NOW).state_at(NOW) is ChallengeState.VERIFIED

    def test_it_cannot_be_used_twice(self) -> None:
        with pytest.raises(InvariantError, match="once"):
            a_challenge().verified(NOW).verified(NOW)

    def test_a_used_challenge_stays_used_after_it_expires(self) -> None:
        # So that a replay is refused as "already used" rather than "too late". The two are
        # different events, and only one of them is somebody attacking.
        used = a_challenge().verified(NOW)
        assert used.state_at(NOW + timedelta(days=1)) is ChallengeState.VERIFIED

    def test_the_code_itself_is_never_held(self) -> None:
        # The property that makes a leaked database useless for signing in: nothing here can
        # tell anybody what the code was, including this system.
        assert not hasattr(a_challenge(), "code")

    def test_a_challenge_with_no_hash_is_refused(self) -> None:
        with pytest.raises(InvariantError):
            a_challenge(code_hash="  ")

    def test_a_challenge_that_expires_before_it_is_issued_is_refused(self) -> None:
        with pytest.raises(InvariantError):
            a_challenge(expires_at=NOW - timedelta(seconds=1))

    def test_negative_attempts_are_refused(self) -> None:
        with pytest.raises(InvariantError):
            a_challenge(attempts=-1)


class TestRefreshTokens:
    def test_a_fresh_token_is_usable(self) -> None:
        assert a_token().is_usable_at(NOW)

    def test_an_expired_token_is_not(self) -> None:
        assert not a_token().is_usable_at(NOW + timedelta(days=31))

    def test_a_rotated_token_is_not_usable_and_is_recognisable(self) -> None:
        # Presenting one of these is the signal that a copy exists somewhere it should not.
        rotated = a_token().rotated(NOW)
        assert not rotated.is_usable_at(NOW)
        assert rotated.was_already_used

    def test_a_revoked_token_is_not_usable(self) -> None:
        assert not a_token().revoked(NOW).is_usable_at(NOW)

    def test_revoking_twice_keeps_the_first_moment(self) -> None:
        # When the family was ended matters for working out what happened afterwards.
        first = a_token().revoked(NOW)
        again = first.revoked(NOW + timedelta(hours=1))
        assert again.revoked_at == NOW

    def test_tokens_from_one_sign_in_share_a_family(self) -> None:
        original = a_token()
        successor = a_token(id="token-2", family_id=original.family_id)
        assert successor.family_id == original.family_id

    def test_a_token_with_no_hash_is_refused(self) -> None:
        with pytest.raises(InvariantError):
            a_token(token_hash="")

    def test_a_token_that_expires_before_it_is_issued_is_refused(self) -> None:
        with pytest.raises(InvariantError):
            a_token(expires_at=NOW)


class TestTokenPairs:
    def test_a_pair_carries_both_halves_and_a_lifetime(self) -> None:
        pair = TokenPair("access", "refresh", 900)
        assert pair.expires_in_seconds == 900

    @pytest.mark.parametrize(("access", "refresh"), [("", "r"), ("a", ""), ("  ", "r")])
    def test_a_pair_missing_a_half_is_refused(self, access: str, refresh: str) -> None:
        with pytest.raises(InvariantError):
            TokenPair(access, refresh, 900)

    def test_an_already_expired_access_token_is_refused(self) -> None:
        with pytest.raises(InvariantError):
            TokenPair("access", "refresh", 0)

    def test_neither_token_appears_when_it_is_rendered(self) -> None:
        # A token pair in a log line is a sign-in somebody can replay.
        rendered = repr(TokenPair("the-access-token", "the-refresh-token", 900))
        assert "the-access-token" not in rendered
        assert "the-refresh-token" not in rendered
        assert "900" in rendered


class TestAuthenticatedUser:
    def test_it_is_valid_inside_its_window(self) -> None:
        user = AuthenticatedUser(UserId("u1"), NOW, NOW + timedelta(minutes=15))
        assert user.is_valid_at(NOW)
        assert user.is_valid_at(NOW + timedelta(minutes=14))

    def test_it_is_not_valid_at_or_after_expiry(self) -> None:
        user = AuthenticatedUser(UserId("u1"), NOW, NOW + timedelta(minutes=15))
        assert not user.is_valid_at(NOW + timedelta(minutes=15))

    def test_it_is_not_valid_before_it_was_issued(self) -> None:
        # A token issued in the future is a clock problem or a forgery, and either way it is
        # not something to accept.
        user = AuthenticatedUser(UserId("u1"), NOW, NOW + timedelta(minutes=15))
        assert not user.is_valid_at(NOW - timedelta(seconds=1))
