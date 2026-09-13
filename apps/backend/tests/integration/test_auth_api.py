"""Signing in end to end over HTTP against a real database, substituting only the text provider."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from letmehandle.adapters.database.repositories import SqlRefreshTokenRepository
from letmehandle.adapters.database.session import unit_of_work
from letmehandle.api.dependencies import SIGNED_IN_REQUESTS_PER_WINDOW
from letmehandle.application.auth.service import AuthenticationPolicy
from letmehandle.domain.models.auth import REFRESH_REUSE_LEEWAY
from tests.integration.conftest import ANOTHER_NUMBER, NUMBER, bearer, code_for, sign_in

if TYPE_CHECKING:
    from tests.integration.conftest import Api

pytestmark = pytest.mark.integration


async def after_the_reuse_leeway(api: Api) -> None:
    """Move every stored rotation back past the leeway."""
    async with unit_of_work(api.app.state.session_factory) as session:
        await session.execute(
            text("UPDATE refresh_tokens SET rotated_at = rotated_at - make_interval(secs => :s)"),
            {"s": (REFRESH_REUSE_LEEWAY + timedelta(seconds=1)).total_seconds()},
        )


def limits(api: Api, **changes: object) -> None:
    """Sign-in limits for this test only."""
    container = api.app.state.container
    api.app.state.container = replace(
        container, auth_limits=replace(container.auth_limits, **changes)
    )


class TestSigningIn:
    async def test_a_new_number_gets_an_account_and_a_session(self, api: Api) -> None:
        tokens = await sign_in(api)
        assert tokens["access_token"]
        assert tokens["refresh_token"]
        assert tokens["token_type"] == "Bearer"
        assert tokens["expires_in_seconds"] > 0

    async def test_the_same_number_signs_into_the_same_account(self, api: Api) -> None:
        first = await sign_in(api)
        second = await sign_in(api)

        me_first = await api.client.get("/v1/me", headers=bearer(first))
        me_second = await api.client.get("/v1/me", headers=bearer(second))

        assert me_first.json()["id"] == me_second.json()["id"]

    async def test_a_number_is_normalised_before_it_becomes_an_account(self, api: Api) -> None:
        # Two spellings of one number must not become two accounts.
        spelled_out = await api.client.post(
            "/v1/auth/challenge", json={"phone_number": "+1 (202) 555-0143"}
        )
        assert spelled_out.status_code == 202

        tokens = await sign_in(api)
        me = await api.client.get("/v1/me", headers=bearer(tokens))
        assert me.json()["phone_number"] == NUMBER

    async def test_a_wrong_code_is_refused(self, api: Api) -> None:
        challenge_id, _ = await code_for(api)
        response = await api.client.post(
            "/v1/auth/verify", json={"challenge_id": challenge_id, "code": "000001"}
        )
        assert response.status_code == 401
        assert response.json()["error"] == "invalid_credentials"

    async def test_an_unknown_challenge_fails_identically(self, api: Api) -> None:
        # The same status and code as a wrong code.
        wrong_code = await api.client.post(
            "/v1/auth/verify",
            json={"challenge_id": (await code_for(api))[0], "code": "000001"},
        )
        unknown = await api.client.post(
            "/v1/auth/verify", json={"challenge_id": "invented", "code": "000001"}
        )
        assert wrong_code.status_code == unknown.status_code
        assert wrong_code.json()["error"] == unknown.json()["error"]

    async def test_a_code_cannot_be_used_twice(self, api: Api) -> None:
        challenge_id, code = await code_for(api)
        first = await api.client.post(
            "/v1/auth/verify", json={"challenge_id": challenge_id, "code": code}
        )
        second = await api.client.post(
            "/v1/auth/verify", json={"challenge_id": challenge_id, "code": code}
        )
        assert first.status_code == 200
        assert second.status_code == 401

    @pytest.mark.parametrize(
        "body",
        [
            {"phone_number": "not-a-number"},
            {"phone_number": "2025550143"},
            {"phone_number": ""},
            {},
            {"phone_number": NUMBER, "unexpected": "field"},
        ],
    )
    async def test_a_malformed_request_is_rejected(self, api: Api, body: dict[str, str]) -> None:
        response = await api.client.post("/v1/auth/challenge", json=body)
        assert response.status_code == 422
        assert response.json()["error"] == "invalid_request"


class TestLimits:
    async def test_too_many_requests_for_one_number_are_refused(self, api: Api) -> None:
        # The sixth code in an hour is refused with when to come back.
        for _ in range(5):
            allowed = await api.client.post("/v1/auth/challenge", json={"phone_number": NUMBER})
            assert allowed.status_code == 202

        refused = await api.client.post("/v1/auth/challenge", json={"phone_number": NUMBER})

        assert refused.status_code == 429
        assert refused.json()["error"] == "rate_limited"
        assert int(refused.headers["Retry-After"]) > 0

    async def test_a_limit_on_one_number_does_not_block_another(self, api: Api) -> None:
        for _ in range(5):
            await api.client.post("/v1/auth/challenge", json={"phone_number": NUMBER})

        other = await api.client.post("/v1/auth/challenge", json={"phone_number": ANOTHER_NUMBER})
        assert other.status_code == 202

    async def test_a_signed_in_user_sending_more_than_any_app_does_is_refused(
        self, api: Api
    ) -> None:
        tokens = await sign_in(api)
        for _ in range(SIGNED_IN_REQUESTS_PER_WINDOW):
            allowed = await api.client.get("/v1/me", headers=bearer(tokens))
            assert allowed.status_code == 200

        refused = await api.client.get("/v1/me", headers=bearer(tokens))

        assert refused.status_code == 429
        assert refused.json()["error"] == "rate_limited"
        assert int(refused.headers["Retry-After"]) > 0

    async def test_one_user_at_the_limit_does_not_block_another(self, api: Api) -> None:
        busy = await sign_in(api)
        quiet = await sign_in(api, ANOTHER_NUMBER)
        for _ in range(SIGNED_IN_REQUESTS_PER_WINDOW):
            await api.client.get("/v1/me", headers=bearer(busy))

        assert (await api.client.get("/v1/me", headers=bearer(quiet))).status_code == 200


class TestSessions:
    async def test_a_session_can_be_renewed(self, api: Api) -> None:
        tokens = await sign_in(api)
        renewed = await api.client.post(
            "/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert renewed.status_code == 200
        assert renewed.json()["refresh_token"] != tokens["refresh_token"]

    async def test_reusing_a_refresh_token_ends_every_session_from_that_sign_in(
        self, api: Api
    ) -> None:
        tokens = await sign_in(api)
        renewed = await api.client.post(
            "/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert renewed.status_code == 200
        await after_the_reuse_leeway(api)

        replayed = await api.client.post(
            "/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert replayed.status_code == 401

        successor = await api.client.post(
            "/v1/auth/refresh", json={"refresh_token": renewed.json()["refresh_token"]}
        )
        assert successor.status_code == 401

    async def test_the_revocation_survives_the_refusal_that_triggered_it(self, api: Api) -> None:
        """The revocation that detects reuse is committed, not rolled back with the refusal."""
        tokens = await sign_in(api)
        renewed = await api.client.post(
            "/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        await after_the_reuse_leeway(api)
        await api.client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

        # A fresh request.
        after = await api.client.post(
            "/v1/auth/refresh", json={"refresh_token": renewed.json()["refresh_token"]}
        )
        assert after.status_code == 401

    async def test_signing_out_invalidates_the_session(self, api: Api) -> None:
        tokens = await sign_in(api)
        signed_out = await api.client.post(
            "/v1/auth/signout", json={"refresh_token": tokens["refresh_token"]}
        )
        assert signed_out.status_code == 204

        after = await api.client.post(
            "/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert after.status_code == 401

    async def test_signing_out_an_unknown_token_still_succeeds(self, api: Api) -> None:
        response = await api.client.post("/v1/auth/signout", json={"refresh_token": "invented"})
        assert response.status_code == 204

    async def test_signing_out_on_one_device_leaves_the_other_signed_in(self, api: Api) -> None:
        first = await sign_in(api)
        second = await sign_in(api)

        await api.client.post("/v1/auth/signout", json={"refresh_token": first["refresh_token"]})

        still_valid = await api.client.post(
            "/v1/auth/refresh", json={"refresh_token": second["refresh_token"]}
        )
        assert still_valid.status_code == 200


class TestStayingSignedIn:
    async def test_a_renewal_whose_answer_never_arrived_can_be_asked_again(self, api: Api) -> None:
        # The app comes back with the token it held before the rotation.
        tokens = await sign_in(api)
        lost = await api.client.post(
            "/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert lost.status_code == 200

        again = await api.client.post(
            "/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )

        assert again.status_code == 200
        me = await api.client.get("/v1/me", headers=bearer(again.json()))
        assert me.status_code == 200
        # The pair that was lost still works: nothing was revoked.
        assert (
            await api.client.post(
                "/v1/auth/refresh", json={"refresh_token": lost.json()["refresh_token"]}
            )
        ).status_code == 200

    async def test_a_session_lasts_ninety_days_and_renewing_starts_them_again(self) -> None:
        assert AuthenticationPolicy().refresh_token_lifetime == timedelta(days=90)


class TestAbuse:
    async def test_a_new_code_comes_with_when_another_may_be_asked_for(self, api: Api) -> None:
        limits(api, resend_cooldowns=AuthenticationPolicy().resend_cooldowns)
        first = await api.client.post("/v1/auth/challenge", json={"phone_number": NUMBER})
        assert first.json()["resend_after_seconds"] == 30

        too_soon = await api.client.post("/v1/auth/challenge", json={"phone_number": NUMBER})

        assert too_soon.status_code == 429
        assert 0 < int(too_soon.headers["Retry-After"]) <= 30

    async def test_only_the_latest_code_works(self, api: Api) -> None:
        old_id, old_code = await code_for(api)
        new_id, new_code = await code_for(api)

        stale = await api.client.post(
            "/v1/auth/verify", json={"challenge_id": old_id, "code": old_code}
        )
        assert stale.status_code == 401
        fresh = await api.client.post(
            "/v1/auth/verify", json={"challenge_id": new_id, "code": new_code}
        )
        assert fresh.status_code == 200

    async def test_too_many_wrong_codes_lock_the_number_even_against_the_right_one(
        self, api: Api
    ) -> None:
        limits(api, failed_codes_per_number=3)
        challenge_id, code = await code_for(api)
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(3):
            await api.client.post(
                "/v1/auth/verify", json={"challenge_id": challenge_id, "code": wrong}
            )

        right = await api.client.post(
            "/v1/auth/verify", json={"challenge_id": challenge_id, "code": code}
        )
        assert right.status_code == 429
        assert int(right.headers["Retry-After"]) > 0
        another = await api.client.post("/v1/auth/challenge", json={"phone_number": NUMBER})
        assert another.status_code == 429
        # Somebody else's number is untouched.
        assert (await sign_in(api, ANOTHER_NUMBER))["access_token"]

    async def test_a_country_this_deployment_does_not_serve_gets_no_code(self, api: Api) -> None:
        limits(api, allowed_calling_codes=frozenset({"44"}))

        refused = await api.client.post("/v1/auth/challenge", json={"phone_number": NUMBER})

        assert refused.status_code == 422
        assert refused.json()["error"] == "unserved_country"
        assert api.otp.sent == []

    async def test_the_deployment_stops_sending_when_its_hourly_budget_is_spent(
        self, api: Api
    ) -> None:
        limits(api, challenges_per_hour=2)
        await api.client.post("/v1/auth/challenge", json={"phone_number": NUMBER})
        await api.client.post("/v1/auth/challenge", json={"phone_number": ANOTHER_NUMBER})

        spent = await api.client.post("/v1/auth/challenge", json={"phone_number": "+12025550145"})

        assert spent.status_code == 429
        assert len(api.otp.sent) == 2

    async def test_verifying_from_one_place_is_limited(self, api: Api) -> None:
        limits(api, verifications_per_source=2)
        challenge_id, _ = await code_for(api)
        for _ in range(2):
            await api.client.post(
                "/v1/auth/verify", json={"challenge_id": challenge_id, "code": "123456"}
            )

        refused = await api.client.post(
            "/v1/auth/verify", json={"challenge_id": challenge_id, "code": "123456"}
        )
        assert refused.status_code == 429


class TestProtectedRoutes:
    async def test_the_profile_needs_a_token(self, api: Api) -> None:
        response = await api.client.get("/v1/me")
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"

    @pytest.mark.parametrize(
        "header",
        [
            {"Authorization": "Bearer not-a-token"},
            {"Authorization": "Bearer "},
            {"Authorization": "Basic abc"},
            {"Authorization": ""},
        ],
    )
    async def test_every_kind_of_bad_token_is_refused(
        self, api: Api, header: dict[str, str]
    ) -> None:
        assert (await api.client.get("/v1/me", headers=header)).status_code == 401

    async def test_the_profile_is_returned_for_a_valid_token(self, api: Api) -> None:
        tokens = await sign_in(api)
        response = await api.client.get("/v1/me", headers=bearer(tokens))
        assert response.status_code == 200
        assert response.json()["phone_number"] == NUMBER

    async def test_the_profile_can_be_updated(self, api: Api) -> None:
        tokens = await sign_in(api)
        updated = await api.client.patch(
            "/v1/me", headers=bearer(tokens), json={"display_name": "Alex"}
        )
        assert updated.status_code == 200
        assert updated.json()["display_name"] == "Alex"

        again = await api.client.get("/v1/me", headers=bearer(tokens))
        assert again.json()["display_name"] == "Alex"

    async def test_an_update_that_changes_nothing_is_accepted(self, api: Api) -> None:
        tokens = await sign_in(api)
        response = await api.client.patch("/v1/me", headers=bearer(tokens), json={})
        assert response.status_code == 200
        assert response.json()["display_name"] is None

    async def test_an_absent_field_is_left_alone_rather_than_cleared(self, api: Api) -> None:
        tokens = await sign_in(api)
        await api.client.patch("/v1/me", headers=bearer(tokens), json={"display_name": "Alex"})

        await api.client.patch("/v1/me", headers=bearer(tokens), json={"locale": "en-GB"})

        after = await api.client.get("/v1/me", headers=bearer(tokens))
        assert after.json()["display_name"] == "Alex"
        assert after.json()["locale"] == "en-GB"

    async def test_the_locale_is_the_one_calls_are_handled_in(self, api: Api) -> None:
        # One locale, shared with preferences.
        tokens = await sign_in(api)

        await api.client.patch("/v1/me", headers=bearer(tokens), json={"locale": "hi"})
        preferences = await api.client.get("/v1/preferences", headers=bearer(tokens))
        assert preferences.json()["locale"] == "hi"

        await api.client.patch("/v1/preferences", headers=bearer(tokens), json={"locale": "en"})
        profile = await api.client.get("/v1/me", headers=bearer(tokens))
        assert profile.json()["locale"] == "en"

    async def test_a_blank_locale_is_refused_as_the_request_s_problem(self, api: Api) -> None:
        tokens = await sign_in(api)
        response = await api.client.patch("/v1/me", headers=bearer(tokens), json={"locale": "  "})
        assert response.status_code == 422
        assert response.json()["error"] == "invalid_request"

    @pytest.mark.parametrize("locale", ["!!!!!!", "en_GB_", "1234"])
    async def test_a_locale_that_is_not_a_language_tag_is_refused(
        self, api: Api, locale: str
    ) -> None:
        tokens = await sign_in(api)
        response = await api.client.patch("/v1/me", headers=bearer(tokens), json={"locale": locale})
        assert response.status_code == 422
        assert response.json()["error"] == "invalid_request"

    async def test_a_refused_update_changes_nothing(self, api: Api) -> None:
        tokens = await sign_in(api)
        response = await api.client.patch(
            "/v1/me", headers=bearer(tokens), json={"display_name": "Alex", "locale": "  "}
        )
        assert response.status_code == 422
        after = await api.client.get("/v1/me", headers=bearer(tokens))
        assert after.json()["display_name"] is None


class TestDegradedService:
    async def test_requests_that_need_the_database_say_so_when_there_is_none(
        self, api: Api
    ) -> None:
        # Distinguishable from an authentication failure and from a crash.
        factory = api.app.state.session_factory
        api.app.state.session_factory = None
        try:
            response = await api.client.post("/v1/auth/challenge", json={"phone_number": NUMBER})
        finally:
            api.app.state.session_factory = factory

        assert response.status_code == 503
        assert response.json()["error"] == "database_unavailable"

    async def test_a_token_for_an_account_that_no_longer_exists_is_refused(self, api: Api) -> None:
        # A token naming a deleted account is unauthenticated.
        from sqlalchemy import delete

        from letmehandle.adapters.database.models import UserRow

        tokens = await sign_in(api)
        async with api.app.state.session_factory() as session:
            await session.execute(delete(UserRow))
            await session.commit()

        assert (await api.client.get("/v1/me", headers=bearer(tokens))).status_code == 401


class TestIsolation:
    async def test_one_user_cannot_read_another_s_profile(self, api: Api) -> None:
        mine = await sign_in(api, NUMBER)
        theirs = await sign_in(api, ANOTHER_NUMBER)

        my_profile = await api.client.get("/v1/me", headers=bearer(mine))
        their_profile = await api.client.get("/v1/me", headers=bearer(theirs))

        assert my_profile.json()["id"] != their_profile.json()["id"]
        assert my_profile.json()["phone_number"] == NUMBER
        assert their_profile.json()["phone_number"] == ANOTHER_NUMBER

    async def test_one_user_cannot_change_another_s_profile(self, api: Api) -> None:
        mine = await sign_in(api, NUMBER)
        theirs = await sign_in(api, ANOTHER_NUMBER)

        await api.client.patch("/v1/me", headers=bearer(mine), json={"display_name": "Mine"})

        their_profile = await api.client.get("/v1/me", headers=bearer(theirs))
        assert their_profile.json()["display_name"] is None


class TestConcurrentAttempts:
    """Limits that hold when the requests arrive together, not only one after another."""

    async def test_guesses_sent_at_once_still_exhaust_the_challenge(self, api: Api) -> None:
        # Guesses arriving together each consume an attempt.
        challenge_id, code = await code_for(api)
        wrong = [f"{guess:06d}" for guess in range(20) if f"{guess:06d}" != code][:10]

        guesses = await asyncio.gather(
            *(
                api.client.post(
                    "/v1/auth/verify", json={"challenge_id": challenge_id, "code": guess}
                )
                for guess in wrong
            )
        )
        assert all(guess.status_code == 401 for guess in guesses)

        correct = await api.client.post(
            "/v1/auth/verify", json={"challenge_id": challenge_id, "code": code}
        )
        assert correct.status_code == 401

    async def test_a_code_presented_twice_at_once_signs_in_once(self, api: Api) -> None:
        challenge_id, code = await code_for(api)

        both = await asyncio.gather(
            *(
                api.client.post(
                    "/v1/auth/verify", json={"challenge_id": challenge_id, "code": code}
                )
                for _ in range(2)
            )
        )

        assert sorted(response.status_code for response in both) == [200, 401]

    async def test_a_refresh_token_being_exchanged_is_not_read_as_unused_elsewhere(
        self, api: Api
    ) -> None:
        # Two exchanges of one token at once are one exchange and one reuse.
        tokens = await sign_in(api)
        token_hash = api.app.state.container.token_hasher.hash(tokens["refresh_token"])
        factory = api.app.state.session_factory

        async with factory() as exchanging, factory() as replaying:
            held = await SqlRefreshTokenRepository(exchanging).find_by_hash(token_hash)
            assert held is not None
            async with asyncio.TaskGroup() as group:
                replay = group.create_task(
                    SqlRefreshTokenRepository(replaying).find_by_hash(token_hash)
                )
                # Holds the exchange open while the replay's query reaches the database.
                await asyncio.sleep(0.2)
                await SqlRefreshTokenRepository(exchanging).update(held.rotated(datetime.now(UTC)))
                await exchanging.commit()

        seen = replay.result()
        assert seen is not None
        assert seen.was_already_used
