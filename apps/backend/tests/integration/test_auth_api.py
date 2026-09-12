"""Signing in, end to end over HTTP against a real database.

Everything here goes through the same stack a mobile app would: the routes, the dependency
wiring, the real hashers, the real signer, and PostgreSQL. The only substitute is the provider
that would send a text message.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.integration.conftest import ANOTHER_NUMBER, NUMBER, bearer, code_for, sign_in

if TYPE_CHECKING:
    from tests.integration.conftest import Api

pytestmark = pytest.mark.integration


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
        # Same status and same code as a wrong code. The difference would tell an attacker
        # whether a challenge identifier they hold is real.
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
        # The default allows five per hour per number. The sixth is refused, and says when to
        # come back rather than leaving a client to retry immediately.
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
        # The behaviour that turns a stolen refresh token from indefinite access into one use
        # and an alarm.
        tokens = await sign_in(api)
        renewed = await api.client.post(
            "/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert renewed.status_code == 200

        replayed = await api.client.post(
            "/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert replayed.status_code == 401

        successor = await api.client.post(
            "/v1/auth/refresh", json={"refresh_token": renewed.json()["refresh_token"]}
        )
        assert successor.status_code == 401

    async def test_the_revocation_survives_the_refusal_that_triggered_it(self, api: Api) -> None:
        """The subtle half of reuse detection.

        The revocation happens on the way to refusing the request. If the refusal rolled the
        transaction back — which is what an ordinary error path does — the family would be
        revoked in memory and left working in the database, and the stolen token would go on
        working with nothing to show it had been noticed.
        """
        tokens = await sign_in(api)
        renewed = await api.client.post(
            "/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        await api.client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

        # A fresh request, so nothing is carried over from the one that detected the reuse.
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
        # A client sending an empty change is not an error, and must not write a row for it.
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


class TestDegradedService:
    async def test_requests_that_need_the_database_say_so_when_there_is_none(
        self, api: Api
    ) -> None:
        # Distinguishable from an authentication failure and from a crash: an operator reading
        # this knows the service is up and its database is not.
        factory = api.app.state.session_factory
        api.app.state.session_factory = None
        try:
            response = await api.client.post("/v1/auth/challenge", json={"phone_number": NUMBER})
        finally:
            api.app.state.session_factory = factory

        assert response.status_code == 503
        assert response.json()["error"] == "database_unavailable"

    async def test_a_token_for_an_account_that_no_longer_exists_is_refused(self, api: Api) -> None:
        # A token outlives the account it names when the account is deleted. Treating that as
        # unauthenticated rather than as a missing row keeps the answer the same as every other
        # failure to authenticate.
        from sqlalchemy import delete

        from letmehandle.adapters.database.models import UserRow

        tokens = await sign_in(api)
        async with api.app.state.session_factory() as session:
            await session.execute(delete(UserRow))
            await session.commit()

        assert (await api.client.get("/v1/me", headers=bearer(tokens))).status_code == 401


class TestIsolation:
    async def test_one_user_cannot_read_another_s_profile(self, api: Api) -> None:
        # The proof every later phase repeats for every resource it adds.
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
