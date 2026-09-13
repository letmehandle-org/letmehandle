"""Sign-in challenges, refresh tokens and token pairs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.auth import (
    CHALLENGE_LIFETIME,
    MAX_ATTEMPTS,
    REFRESH_REUSE_LEEWAY,
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

    def test_it_closes_when_it_expires(self) -> None:
        challenge = a_challenge()
        assert challenge.state_at(NOW + CHALLENGE_LIFETIME) is ChallengeState.EXPIRED
        assert not challenge.is_open_at(NOW + CHALLENGE_LIFETIME)

    def test_it_closes_when_the_attempts_run_out(self) -> None:
        challenge = a_challenge()
        for _ in range(MAX_ATTEMPTS):
            challenge = challenge.with_failed_attempt()
        assert challenge.state_at(NOW) is ChallengeState.EXHAUSTED

    def test_using_it_marks_it_verified(self) -> None:
        assert a_challenge().verified(NOW).state_at(NOW) is ChallengeState.VERIFIED

    def test_it_cannot_be_used_twice(self) -> None:
        with pytest.raises(InvariantError, match="once"):
            a_challenge().verified(NOW).verified(NOW)

    def test_a_used_challenge_stays_used_after_it_expires(self) -> None:
        used = a_challenge().verified(NOW)
        assert used.state_at(NOW + timedelta(days=1)) is ChallengeState.VERIFIED

    def test_the_code_itself_is_never_held(self) -> None:
        assert not hasattr(a_challenge(), "code")

    def test_a_challenge_with_an_empty_hash_is_refused(self) -> None:
        with pytest.raises(InvariantError):
            a_challenge(code_hash="  ")

    def test_a_code_the_provider_holds_keeps_every_other_rule(self) -> None:
        held = a_challenge(code_hash=None)
        assert held.code_is_held_by_provider
        assert not a_challenge().code_is_held_by_provider
        assert held.with_failed_attempt().code_hash is None
        assert held.state_at(NOW + CHALLENGE_LIFETIME) is ChallengeState.EXPIRED
        used = held.verified(NOW)
        assert used.code_is_held_by_provider
        with pytest.raises(InvariantError, match="only be used once"):
            used.verified(NOW)
        assert held.superseded(NOW).state_at(NOW) is ChallengeState.SUPERSEDED

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
        rotated = a_token().rotated(NOW)
        assert not rotated.is_usable_at(NOW)
        assert rotated.was_already_used

    def test_a_revoked_token_is_not_usable(self) -> None:
        assert not a_token().revoked(NOW).is_usable_at(NOW)

    def test_revoking_twice_keeps_the_first_moment(self) -> None:
        first = a_token().revoked(NOW)
        again = first.revoked(NOW + timedelta(hours=1))
        assert again.revoked_at == NOW

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
        user = AuthenticatedUser(UserId("u1"), NOW, NOW + timedelta(minutes=15))
        assert not user.is_valid_at(NOW - timedelta(seconds=1))


class TestSupersession:
    def test_a_superseded_challenge_is_closed_even_before_it_expires(self) -> None:
        closed = a_challenge().superseded(NOW)
        assert closed.state_at(NOW) is ChallengeState.SUPERSEDED
        assert not closed.is_open_at(NOW)

    def test_a_finished_challenge_is_left_as_it_was(self) -> None:
        used = a_challenge().verified(NOW)
        assert used.superseded(NOW) is used
        closed = a_challenge().superseded(NOW)
        assert closed.superseded(NOW + timedelta(seconds=1)) is closed

    def test_attempts_carry_the_supersession_with_them(self) -> None:
        closed = a_challenge().superseded(NOW)
        assert closed.with_failed_attempt().superseded_at == NOW

    def test_the_right_code_is_not_a_failed_attempt(self) -> None:
        guessed = a_challenge().with_failed_attempt().with_failed_attempt()
        assert guessed.failed_attempts == 2
        assert guessed.verified(NOW).failed_attempts == 2
        assert guessed.verified(NOW).superseded_at is None


class TestReuseLeeway:
    def test_a_token_rotated_moments_ago_is_within_it(self) -> None:
        rotated = a_token().rotated(NOW)
        assert rotated.is_within_reuse_leeway_at(NOW + REFRESH_REUSE_LEEWAY)
        assert not rotated.is_within_reuse_leeway_at(
            NOW + REFRESH_REUSE_LEEWAY + timedelta(seconds=1)
        )

    def test_an_unrotated_revoked_or_expired_token_is_not(self) -> None:
        assert not a_token().is_within_reuse_leeway_at(NOW)
        assert not a_token().rotated(NOW).revoked(NOW).is_within_reuse_leeway_at(NOW)
        expiring = a_token(expires_at=NOW + timedelta(seconds=30)).rotated(NOW)
        assert not expiring.is_within_reuse_leeway_at(NOW + timedelta(seconds=31))
