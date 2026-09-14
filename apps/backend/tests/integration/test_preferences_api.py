"""Preferences over HTTP against a real database, where an absent section is left as it was."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import text

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
    "active": {"start": "07:00", "end": "22:00", "zone": "Europe/London"},
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
        # Nothing granted, nothing disclosed.
        assert preferences["authority"]["capabilities"] == []
        assert preferences["important_contacts"] == []
        assert preferences["hours"]["active"] is None
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
                    "respect_active_hours": False,
                },
                "personality": {
                    "formality": "formal",
                    "verbosity": "detailed",
                    "topics": ["school run", "deliveries"],
                    "disclosable_facts": ["Works from home on Tuesdays"],
                },
                "important_contacts": [{"phone_number": ANOTHER_NUMBER, "label": "The school"}],
            },
        )

        after = await read(api, tokens)
        assert after["locale"] == "en-GB"
        assert after["hours"]["active"]["zone"] == "Europe/London"
        assert sorted(after["authority"]["capabilities"]) == [
            "confirm_appointments",
            "take_a_message",
        ]
        assert after["notifications"]["daily_summary"] is True
        assert after["personality"]["topics"] == ["deliveries", "school run"]
        assert after["personality"]["disclosable_facts"] == ["Works from home on Tuesdays"]
        assert after["important_contacts"][0]["label"] == "The school"

    async def test_hours_can_be_cleared(self, api: Api) -> None:
        # Absent leaves alone; explicitly empty clears.
        tokens = await sign_in(api)
        await patch(api, tokens, {"hours": LONDON_HOURS})

        await patch(api, tokens, {"hours": {"active": None}})

        assert (await read(api, tokens))["hours"] == {"active": None}

    async def test_a_new_user_s_assistant_works_around_the_clock(self, api: Api) -> None:
        tokens = await sign_in(api)
        assert (await read(api, tokens))["hours"] == {"active": None}

    async def test_the_two_windows_of_the_old_shape_are_refused(self, api: Api) -> None:
        # Only the one-window shape is accepted (D-030).
        tokens = await sign_in(api)
        response = await api.client.patch(
            "/v1/preferences",
            headers=bearer(tokens),
            json={"hours": {"quiet": {"start": "22:00", "end": "07:00", "zone": "UTC"}}},
        )
        assert response.status_code == 422

    async def test_a_number_is_normalised_before_it_is_stored(self, api: Api) -> None:
        tokens = await sign_in(api)
        await patch(
            api,
            tokens,
            {"important_contacts": [{"phone_number": "+1 (202) 555-0144", "label": "Mum"}]},
        )
        assert (await read(api, tokens))["important_contacts"][0]["phone_number"] == ANOTHER_NUMBER

    async def test_the_response_is_stable_for_the_same_stored_values(self, api: Api) -> None:
        # Sets are sorted on the way out.
        tokens = await sign_in(api)
        await patch(
            api,
            tokens,
            {"personality": {"formality": "warm", "verbosity": "brief", "topics": ["b", "a"]}},
        )
        assert await read(api, tokens) == await read(api, tokens)


class TestTheTwoHalvesOfOneDomainObject:
    """Call handling and hours share one `CallRules`, and saving one leaves the other."""

    async def test_saving_hours_does_not_reset_call_handling(self, api: Api) -> None:
        tokens = await sign_in(api)
        await patch(api, tokens, {"call_handling": FULL_CALL_HANDLING})

        await patch(api, tokens, {"hours": LONDON_HOURS})

        after = await read(api, tokens)
        assert after["call_handling"]["anonymous_posture"] == "reject"
        assert after["call_handling"]["blocked_categories"] == ["spam"]
        assert after["call_handling"]["escalate_at_or_above"] == 50
        assert after["call_handling"]["posture_by_category"] == {"known_contact": "pass_through"}

    async def test_saving_call_handling_does_not_reset_hours(self, api: Api) -> None:
        tokens = await sign_in(api)
        await patch(api, tokens, {"hours": LONDON_HOURS})

        await patch(api, tokens, {"call_handling": FULL_CALL_HANDLING})

        after = await read(api, tokens)
        assert after["hours"] == LONDON_HOURS


class TestConcurrentSaves:
    # Rounds of two concurrent saves, since one round interleaves only by chance.
    ROUNDS = 20

    async def test_two_sections_saved_at_once_both_survive(self, api: Api) -> None:
        """Two concurrent saves, before and after a first save, both keep their change."""
        import asyncio

        from sqlalchemy import delete

        from letmehandle.adapters.database.models import PreferencesRow

        tokens = await sign_in(api)
        lost = 0
        for round_number in range(self.ROUNDS):
            if round_number % 2 == 0:
                # Every other round starts with no stored preferences.
                async with api.app.state.session_factory() as session:
                    await session.execute(delete(PreferencesRow))
                    await session.commit()

            first, second = await asyncio.gather(
                api.client.patch(
                    "/v1/preferences",
                    headers=bearer(tokens),
                    json={
                        "personality": {"formality": "warm", "verbosity": "brief", "topics": ["a"]}
                    },
                ),
                api.client.patch(
                    "/v1/preferences",
                    headers=bearer(tokens),
                    json={"notifications": {"on_handled_call": True}},
                ),
            )
            assert first.status_code == 200
            assert second.status_code == 200

            after = await read(api, tokens)
            kept = (
                after["personality"]["formality"] == "warm"
                and after["personality"]["topics"] == ["a"]
                and after["notifications"]["on_handled_call"] is True
            )
            lost += not kept
            await patch(
                api,
                tokens,
                {
                    "personality": {"formality": "neutral", "verbosity": "brief", "topics": []},
                    "notifications": {"on_handled_call": False},
                },
            )

        assert lost == 0, f"a change was lost in {lost} of {self.ROUNDS} rounds"


class TestReplacing:
    async def test_put_resets_what_it_does_not_mention(self, api: Api) -> None:
        tokens = await sign_in(api)
        await patch(api, tokens, {"authority": {"capabilities": ["take_a_message"]}})

        response = await api.client.put(
            "/v1/preferences", headers=bearer(tokens), json={"locale": "en-GB"}
        )

        assert response.status_code == 200
        assert response.json()["locale"] == "en-GB"
        assert response.json()["authority"]["capabilities"] == []


class TestValidation:
    async def test_a_category_cannot_be_blocked_and_handled_at_once(self, api: Api) -> None:
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
            {
                "personality": {
                    "formality": "warm",
                    "verbosity": "brief",
                    "disclosable_facts": ["x" * 121],
                }
            },
            {"personality": {"formality": "warm", "verbosity": "brief", "disclosable_facts": [""]}},
            {"locale": ""},
            {"unexpected": "field"},
        ],
    )
    async def test_the_api_refuses_what_the_domain_would(
        self, api: Api, body: dict[str, Any]
    ) -> None:
        # The API refuses what the domain refuses.
        tokens = await sign_in(api)
        response = await api.client.patch("/v1/preferences", headers=bearer(tokens), json=body)
        assert response.status_code == 422, response.text

    @pytest.mark.parametrize(
        ("body", "why"),
        [
            ({"locale": "  "}, "blank once the whitespace is taken off"),
            (
                {
                    "personality": {
                        "formality": "warm",
                        "verbosity": "brief",
                        "topics": [f"topic {number}" for number in range(60)],
                    }
                },
                "more topics than the domain allows",
            ),
            (
                {
                    "important_contacts": [
                        {"phone_number": "+12025550143", "label": "Mum"},
                        {"phone_number": "+1 (202) 555-0143", "label": "Also Mum"},
                    ]
                },
                "one number twice, in two spellings",
            ),
        ],
    )
    async def test_a_whole_set_invariant_is_a_request_problem_not_a_server_fault(
        self, api: Api, body: dict[str, Any], why: str
    ) -> None:
        # Whole-set invariants are a 422, not a 500.
        tokens = await sign_in(api)
        response = await api.client.patch("/v1/preferences", headers=bearer(tokens), json=body)
        assert response.status_code == 422, f"{why}: {response.text}"
        assert response.json()["error"] == "invalid_request"

    async def test_the_same_holds_for_a_replace(self, api: Api) -> None:
        tokens = await sign_in(api)
        response = await api.client.put(
            "/v1/preferences",
            headers=bearer(tokens),
            json={
                "important_contacts": [
                    {"phone_number": "+12025550143", "label": "Mum"},
                    {"phone_number": "+1 (202) 555-0143", "label": "Also Mum"},
                ]
            },
        )
        assert response.status_code == 422

    async def test_a_time_with_seconds_is_refused(self, api: Api) -> None:
        # Stored to the minute, so seconds never make a window's ends equal.
        tokens = await sign_in(api)
        response = await api.client.patch(
            "/v1/preferences",
            headers=bearer(tokens),
            json={"hours": {"working": {"start": "09:00:30", "end": "17:00", "zone": "UTC"}}},
        )
        assert response.status_code == 422

    async def test_a_contact_label_cannot_span_lines(self, api: Api) -> None:
        tokens = await sign_in(api)
        await patch(
            api,
            tokens,
            {
                "important_contacts": [
                    {"phone_number": ANOTHER_NUMBER, "label": "Mum\nand something else"}
                ]
            },
        )
        stored = (await read(api, tokens))["important_contacts"][0]["label"]
        assert "\n" not in stored
        assert stored == "Mum and something else"

    @pytest.mark.parametrize("locale", ["!!!!!!", "en_GB_", "1234"])
    async def test_a_locale_that_is_not_a_language_tag_is_refused(
        self, api: Api, locale: str
    ) -> None:
        tokens = await sign_in(api)
        response = await api.client.patch(
            "/v1/preferences", headers=bearer(tokens), json={"locale": locale}
        )
        assert response.status_code == 422

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
        assert response.json()["next_step"] == "call_handling"
        assert response.json()["is_complete"] is False

    async def test_recording_a_step_moves_to_the_next(self, api: Api) -> None:
        tokens = await sign_in(api)
        response = await api.client.post(
            "/v1/onboarding", headers=bearer(tokens), json={"step": "call_handling"}
        )
        assert response.json()["next_step"] == "hours"

    async def test_progress_survives_a_new_session(self, api: Api) -> None:
        # Held on the server.
        first = await sign_in(api)
        await api.client.post(
            "/v1/onboarding", headers=bearer(first), json={"step": "call_handling"}
        )

        second = await sign_in(api)
        response = await api.client.get("/v1/onboarding", headers=bearer(second))

        assert response.json()["next_step"] == "hours"

    async def test_a_skippable_step_can_be_skipped(self, api: Api) -> None:
        tokens = await sign_in(api)
        await api.client.post(
            "/v1/onboarding", headers=bearer(tokens), json={"step": "call_handling"}
        )

        response = await api.client.post(
            "/v1/onboarding",
            headers=bearer(tokens),
            json={"step": "hours", "skipped": True},
        )

        assert response.json()["next_step"] == "authority"
        assert "hours" in response.json()["skipped"]

    async def test_call_handling_cannot_be_skipped(self, api: Api) -> None:
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
        steps = ["call_handling", "hours", "authority", "notifications"]
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

    @pytest.mark.parametrize("removed", ["introduction", "important_contacts", "personality"])
    async def test_a_step_setup_no_longer_asks_is_refused(self, api: Api, removed: str) -> None:
        # Settings now, not steps (D-032).
        tokens = await sign_in(api)
        response = await api.client.post(
            "/v1/onboarding", headers=bearer(tokens), json={"step": removed}
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

        await api.client.post(
            "/v1/onboarding", headers=bearer(mine), json={"step": "call_handling"}
        )

        response = await api.client.get("/v1/onboarding", headers=bearer(theirs))
        assert response.json()["next_step"] == "call_handling"


class TestTranscriptRetention:
    async def test_a_new_user_keeps_transcripts_for_seven_days(self, api: Api) -> None:
        tokens = await sign_in(api)
        assert (await read(api, tokens))["privacy"] == {"transcript_retention_days": 7}

    async def test_a_change_round_trips_and_leaves_other_sections_alone(self, api: Api) -> None:
        tokens = await sign_in(api)
        await patch(api, tokens, {"locale": "en-GB"})

        updated = await patch(api, tokens, {"privacy": {"transcript_retention_days": 30}})

        assert updated["privacy"]["transcript_retention_days"] == 30
        stored = await read(api, tokens)
        assert stored["privacy"]["transcript_retention_days"] == 30
        assert stored["locale"] == "en-GB"

    async def test_another_section_s_change_keeps_it(self, api: Api) -> None:
        tokens = await sign_in(api)
        await patch(api, tokens, {"privacy": {"transcript_retention_days": 1}})
        await patch(api, tokens, {"locale": "en-GB"})
        assert (await read(api, tokens))["privacy"]["transcript_retention_days"] == 1

    async def test_a_longer_retention_from_a_newer_deployment_survives_an_unrelated_change(
        self, api: Api
    ) -> None:
        tokens = await sign_in(api)
        await patch(api, tokens, {"privacy": {"transcript_retention_days": 30}})
        async with api.app.state.engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE user_preferences SET document = "
                    "jsonb_set(document, '{transcript_retention_days}', '365')"
                )
            )

        await patch(api, tokens, {"locale": "en-GB"})

        assert (await read(api, tokens))["privacy"]["transcript_retention_days"] == 365
        async with api.app.state.engine.connect() as connection:
            stored = await connection.execute(
                text("SELECT document->'transcript_retention_days' FROM user_preferences")
            )
            assert stored.scalar_one() == 365

    async def test_an_empty_privacy_section_is_refused_rather_than_resetting_it(
        self, api: Api
    ) -> None:
        # The section's one field has no default.
        tokens = await sign_in(api)
        await patch(api, tokens, {"privacy": {"transcript_retention_days": 30}})

        response = await api.client.patch(
            "/v1/preferences", headers=bearer(tokens), json={"privacy": {}}
        )

        assert response.status_code == 422, response.text
        assert (await read(api, tokens))["privacy"]["transcript_retention_days"] == 30

    @pytest.mark.parametrize("days", [1, 90])
    async def test_the_floor_and_ceiling_are_accepted(self, api: Api, days: int) -> None:
        tokens = await sign_in(api)
        updated = await patch(api, tokens, {"privacy": {"transcript_retention_days": days}})
        assert updated["privacy"]["transcript_retention_days"] == days

    @pytest.mark.parametrize("days", [0, 91, -1, "7", 7.5, True, None])
    async def test_a_value_outside_them_is_refused(self, api: Api, days: object) -> None:
        tokens = await sign_in(api)
        response = await api.client.patch(
            "/v1/preferences",
            headers=bearer(tokens),
            json={"privacy": {"transcript_retention_days": days}},
        )
        assert response.status_code == 422, response.text
        assert (await read(api, tokens))["privacy"]["transcript_retention_days"] == 7
