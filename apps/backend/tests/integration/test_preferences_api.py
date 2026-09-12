"""Preferences over HTTP, against a real database.

The rule under test throughout: a section nobody sent is left exactly as it was. Every failure
here is the same shape — one screen saving one thing and quietly erasing the rest.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tests.integration.conftest import ANOTHER_NUMBER, bearer, sign_in

if TYPE_CHECKING:
    from tests.integration.conftest import Api

pytestmark = pytest.mark.integration

FULL_CALL_HANDLING: dict[str, Any] = {
    "default_posture": "handle_with_agent",
    "anonymous_posture": "reject",
    "posture_by_category": {"known_contact": "pass_through"},
    "blocked_categories": ["spam"],
    "escalate_at_or_above": 50,
}

LONDON_HOURS: dict[str, Any] = {
    "working": {"start": "09:00", "end": "17:30", "zone": "Europe/London"},
    "quiet": {"start": "22:00", "end": "07:00", "zone": "Europe/London"},
}


async def read(api: Api, tokens: dict[str, Any]) -> dict[str, Any]:
    response = await api.client.get("/v1/preferences", headers=bearer(tokens))
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def patch(api: Api, tokens: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    response = await api.client.patch("/v1/preferences", headers=bearer(tokens), json=body)
    assert response.status_code == 200, response.text
    updated: dict[str, Any] = response.json()
    return updated


class TestReading:
    async def test_a_new_user_gets_the_defaults(self, api: Api) -> None:
        tokens = await sign_in(api)
        preferences = await read(api, tokens)

        assert preferences["version"] >= 1
        # The safest reasonable option everywhere: nothing granted, nothing disclosed.
        assert preferences["authority"]["capabilities"] == []
        assert preferences["important_contacts"] == []
        assert preferences["hours"]["working"] is None
        assert preferences["notifications"]["on_handled_call"] is False

    async def test_it_needs_a_token(self, api: Api) -> None:
        assert (await api.client.get("/v1/preferences")).status_code == 401


class TestPartialUpdates:
    async def test_a_section_that_was_sent_changes(self, api: Api) -> None:
        tokens = await sign_in(api)
        updated = await patch(api, tokens, {"call_handling": FULL_CALL_HANDLING})
        assert updated["call_handling"]["anonymous_posture"] == "reject"

    async def test_a_section_that_was_not_sent_survives(self, api: Api) -> None:
        tokens = await sign_in(api)
        await patch(api, tokens, {"authority": {"capabilities": ["take_a_message"]}})

        await patch(api, tokens, {"personality": {"formality": "warm", "verbosity": "brief"}})

        after = await read(api, tokens)
        assert after["authority"]["capabilities"] == ["take_a_message"]
        assert after["personality"]["formality"] == "warm"

    async def test_every_section_can_be_set_and_read_back(self, api: Api) -> None:
        tokens = await sign_in(api)

        await patch(
            api,
            tokens,
            {
                "locale": "en-GB",
                "call_handling": FULL_CALL_HANDLING,
                "hours": LONDON_HOURS,
                "authority": {"capabilities": ["take_a_message", "confirm_appointments"]},
                "notifications": {
                    "on_handled_call": True,
                    "on_blocked_call": True,
                    "on_missed_escalation": False,
                    "daily_summary": True,
                    "respect_quiet_hours": False,
                },
                "personality": {
                    "formality": "formal",
                    "verbosity": "detailed",
                    "topics": ["school run", "deliveries"],
                },
                "important_contacts": [{"phone_number": ANOTHER_NUMBER, "label": "The school"}],
            },
        )

        after = await read(api, tokens)
        assert after["locale"] == "en-GB"
        assert after["hours"]["quiet"]["zone"] == "Europe/London"
        assert sorted(after["authority"]["capabilities"]) == [
            "confirm_appointments",
            "take_a_message",
        ]
        assert after["notifications"]["daily_summary"] is True
        assert after["personality"]["topics"] == ["deliveries", "school run"]
        assert after["important_contacts"][0]["label"] == "The school"

    async def test_hours_can_be_cleared(self, api: Api) -> None:
        # Absent leaves alone; explicitly empty clears. Without both, quiet hours once set
        # could never be removed.
        tokens = await sign_in(api)
        await patch(api, tokens, {"hours": LONDON_HOURS})

        await patch(api, tokens, {"hours": {"working": None, "quiet": None}})

        assert (await read(api, tokens))["hours"]["working"] is None

    async def test_a_number_is_normalised_before_it_is_stored(self, api: Api) -> None:
        tokens = await sign_in(api)
        await patch(
            api,
            tokens,
            {"important_contacts": [{"phone_number": "+1 (202) 555-0144", "label": "Mum"}]},
        )
        assert (await read(api, tokens))["important_contacts"][0]["phone_number"] == ANOTHER_NUMBER

    async def test_the_response_is_stable_for_the_same_stored_values(self, api: Api) -> None:
        # Sets are sorted on the way out, so a client comparing two reads does not see a change
        # that is not one.
        tokens = await sign_in(api)
        await patch(
            api,
            tokens,
            {"personality": {"formality": "warm", "verbosity": "brief", "topics": ["b", "a"]}},
        )
        assert await read(api, tokens) == await read(api, tokens)


class TestReplacing:
    async def test_put_resets_what_it_does_not_mention(self, api: Api) -> None:
        tokens = await sign_in(api)
        await patch(api, tokens, {"authority": {"capabilities": ["take_a_message"]}})

        response = await api.client.put(
            "/v1/preferences", headers=bearer(tokens), json={"locale": "en-GB"}
        )

        assert response.status_code == 200
        assert response.json()["locale"] == "en-GB"
        # The deliberate difference from patch: replace means replace.
        assert response.json()["authority"]["capabilities"] == []


class TestValidation:
    async def test_a_category_cannot_be_blocked_and_handled_at_once(self, api: Api) -> None:
        # The outcome would depend on which rule was read first, which is a coin toss dressed
        # up as configuration.
        tokens = await sign_in(api)
        response = await api.client.patch(
            "/v1/preferences",
            headers=bearer(tokens),
            json={
                "call_handling": {
                    **FULL_CALL_HANDLING,
                    "blocked_categories": ["sales"],
                    "posture_by_category": {"sales": "pass_through"},
                }
            },
        )
        assert response.status_code == 422
        assert response.json()["error"] == "invalid_request"

    @pytest.mark.parametrize(
        "body",
        [
            {"hours": {"working": {"start": "25:00", "end": "17:00", "zone": "Europe/London"}}},
            {"hours": {"working": {"start": "09:00", "end": "09:00", "zone": "Europe/London"}}},
            {"hours": {"working": {"start": "09:00", "end": "17:00", "zone": "Mars/Olympus"}}},
            {"important_contacts": [{"phone_number": "not-a-number", "label": "x"}]},
            {"important_contacts": [{"phone_number": ANOTHER_NUMBER, "label": ""}]},
            {"personality": {"formality": "brusque", "verbosity": "brief", "topics": []}},
            {"locale": ""},
            {"unexpected": "field"},
        ],
    )
    async def test_the_api_refuses_what_the_domain_would(
        self, api: Api, body: dict[str, Any]
    ) -> None:
        # The API rejects what the UI would, and for the same reasons: the domain type is the
        # single source of truth and this layer constructs it.
        tokens = await sign_in(api)
        response = await api.client.patch("/v1/preferences", headers=bearer(tokens), json=body)
        assert response.status_code == 422, response.text

    async def test_a_rejected_change_stores_nothing(self, api: Api) -> None:
        tokens = await sign_in(api)
        await patch(api, tokens, {"locale": "en-GB"})

        await api.client.patch(
            "/v1/preferences",
            headers=bearer(tokens),
            json={"important_contacts": [{"phone_number": "nonsense", "label": "x"}]},
        )

        assert (await read(api, tokens))["locale"] == "en-GB"


class TestOnboarding:
    async def test_a_new_user_starts_at_the_beginning(self, api: Api) -> None:
        tokens = await sign_in(api)
        response = await api.client.get("/v1/onboarding", headers=bearer(tokens))

        assert response.status_code == 200
        assert response.json()["next_step"] == "introduction"
        assert response.json()["is_complete"] is False

    async def test_recording_a_step_moves_to_the_next(self, api: Api) -> None:
        tokens = await sign_in(api)
        response = await api.client.post(
            "/v1/onboarding", headers=bearer(tokens), json={"step": "introduction"}
        )
        assert response.json()["next_step"] == "call_handling"

    async def test_progress_survives_a_new_session(self, api: Api) -> None:
        # Held on the server, so reinstalling or signing in elsewhere resumes rather than
        # starting again. This is the test that would fail if it were kept on the device.
        first = await sign_in(api)
        await api.client.post(
            "/v1/onboarding", headers=bearer(first), json={"step": "introduction"}
        )

        second = await sign_in(api)
        response = await api.client.get("/v1/onboarding", headers=bearer(second))

        assert response.json()["next_step"] == "call_handling"

    async def test_a_skippable_step_can_be_skipped(self, api: Api) -> None:
        tokens = await sign_in(api)
        for step in ("introduction", "call_handling"):
            await api.client.post("/v1/onboarding", headers=bearer(tokens), json={"step": step})

        response = await api.client.post(
            "/v1/onboarding",
            headers=bearer(tokens),
            json={"step": "important_contacts", "skipped": True},
        )

        assert response.json()["next_step"] == "hours"
        assert "important_contacts" in response.json()["skipped"]

    async def test_call_handling_cannot_be_skipped(self, api: Api) -> None:
        # There is no safe default for an unknown caller, and guessing on somebody's behalf is
        # the one thing this product must not do.
        tokens = await sign_in(api)
        response = await api.client.post(
            "/v1/onboarding",
            headers=bearer(tokens),
            json={"step": "call_handling", "skipped": True},
        )
        assert response.status_code == 422
        assert response.json()["error"] == "invalid_request"

    async def test_finishing_every_step_completes_it(self, api: Api) -> None:
        tokens = await sign_in(api)
        steps = [
            "introduction",
            "call_handling",
            "important_contacts",
            "hours",
            "authority",
            "notifications",
            "personality",
        ]
        for step in steps:
            response = await api.client.post(
                "/v1/onboarding", headers=bearer(tokens), json={"step": step}
            )

        assert response.json()["is_complete"] is True
        assert response.json()["next_step"] is None
        assert response.json()["remaining"] == []

    async def test_an_invented_step_is_refused(self, api: Api) -> None:
        tokens = await sign_in(api)
        response = await api.client.post(
            "/v1/onboarding", headers=bearer(tokens), json={"step": "invented"}
        )
        assert response.status_code == 422

    async def test_it_needs_a_token(self, api: Api) -> None:
        assert (await api.client.get("/v1/onboarding")).status_code == 401


class TestIsolation:
    async def test_one_user_cannot_read_another_s_preferences(self, api: Api) -> None:
        mine = await sign_in(api)
        theirs = await sign_in(api, ANOTHER_NUMBER)

        await patch(api, mine, {"locale": "en-GB"})

        assert (await read(api, theirs))["locale"] == "en"

    async def test_one_user_cannot_change_another_s_preferences(self, api: Api) -> None:
        mine = await sign_in(api)
        theirs = await sign_in(api, ANOTHER_NUMBER)

        await patch(api, mine, {"authority": {"capabilities": ["take_a_message"]}})

        assert (await read(api, theirs))["authority"]["capabilities"] == []

    async def test_onboarding_progress_is_per_user(self, api: Api) -> None:
        mine = await sign_in(api)
        theirs = await sign_in(api, ANOTHER_NUMBER)

        await api.client.post("/v1/onboarding", headers=bearer(mine), json={"step": "introduction"})

        response = await api.client.get("/v1/onboarding", headers=bearer(theirs))
        assert response.json()["next_step"] == "introduction"
