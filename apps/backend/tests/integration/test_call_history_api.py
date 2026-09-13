"""Call history over HTTP, against a real database.

Calls are seeded through the real repositories and committed, as orchestration will leave them,
and read back only through the API. Each test is something a user would notice: a call missing
from their history or shown twice, a stranger's number on their screen, somebody else's call
answering at all, a transcript said to be gone that never existed, or a deleted call that is not.
"""

from __future__ import annotations

import base64
import dataclasses
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import text

from letmehandle.adapters.database.call_repositories import (
    SqlCallRepository,
    SqlSummaryRepository,
    SqlTranscriptRepository,
)
from letmehandle.adapters.database.repositories import SqlEscalationContextRepository
from letmehandle.adapters.database.session import unit_of_work
from letmehandle.application.calls.fallback import CallFacts, fallback_summary
from letmehandle.domain.models.call import (
    CallHandling,
    CallSession,
    Participant,
    ParticipantRole,
    Speaker,
    TranscriptEntry,
)
from letmehandle.domain.models.call_state import CallState, is_terminal
from letmehandle.domain.models.caller import Caller, CallerCategory
from letmehandle.domain.models.escalation import EscalationReason
from letmehandle.domain.models.escalation_context import EscalationContext
from letmehandle.domain.models.identifiers import CallId, UserId
from letmehandle.domain.models.intent import CallImportance, CallIntent
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.summary import CallOutcome, CallSummary, ExtractedDetail
from letmehandle.domain.ports.repositories import MAX_CALL_PAGE
from letmehandle.purge import purge_transcripts
from tests.contracts.fakes import FixedClock
from tests.integration.conftest import ANOTHER_NUMBER, bearer, sign_in
from tests.support.config import make_settings
from tests.support.recording_metrics import RecordingMetrics

if TYPE_CHECKING:
    from httpx import Response

    from letmehandle.domain.ports.security import TranscriptCipher
    from tests.integration.conftest import Api

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
STRANGER_NUMBER = PhoneNumber("+12025550187")
STRANGER = Caller(number=STRANGER_NUMBER, display_name="Prize Desk", category=CallerCategory.SALES)
CONTACT = Caller(
    number=PhoneNumber("+12025550112"), display_name="Sam", category=CallerCategory.KNOWN_CONTACT
)
REASON = EscalationReason.DECISION_NEEDS_THE_USER

# Distinctive, so finding them anywhere they should not be can only mean they leaked.
SAID = "the side gate code is heron-4471"
EVIDENCE = "reference ZX-heron-9043"


def at(seconds: float) -> datetime:
    return NOW + timedelta(seconds=seconds)


@dataclass(frozen=True, slots=True)
class Person:
    user_id: UserId
    headers: dict[str, str]


async def person(api: Api, number: str | None = None) -> Person:
    tokens = await (sign_in(api) if number is None else sign_in(api, number))
    me = await api.client.get("/v1/me", headers=bearer(tokens))
    assert me.status_code == 200, me.text
    return Person(UserId(me.json()["id"]), bearer(tokens))


def a_call(
    owner: Person,
    call_id: str,
    *,
    started_at: datetime = NOW,
    state: CallState = CallState.COMPLETED,
    caller: Caller = STRANGER,
    participants: tuple[Participant, ...] = (),
    lasting: float = 60,
    handling: CallHandling | None = None,
    escalated_at: datetime | None = None,
) -> CallSession:
    return CallSession.restore(
        id=CallId(call_id),
        user_id=owner.user_id,
        caller=caller,
        started_at=started_at,
        state=state,
        participants=participants,
        ended_at=started_at + timedelta(seconds=lasting) if is_terminal(state) else None,
        handling=handling,
        escalated_at=escalated_at,
    )


def escalated_call(owner: Person, call_id: str = "escalated") -> CallSession:
    return a_call(
        owner,
        call_id,
        caller=CONTACT,
        participants=(
            Participant(ParticipantRole.CALLER, NOW, at(90)),
            Participant(ParticipantRole.AGENT, at(2), at(40)),
            Participant(ParticipantRole.HUMAN, at(35), at(90)),
        ),
        lasting=90,
        handling=CallHandling.ASSISTANT,
        escalated_at=at(30),
    )


def a_summary(call: CallSession, **overrides: Any) -> CallSummary:
    assert call.ended_at is not None
    fields: dict[str, Any] = {
        "call_id": call.id,
        "caller": call.caller,
        "intent": CallIntent.DELIVERY_IN_PROGRESS,
        "importance": CallImportance.NOTABLE,
        "outcome": CallOutcome.RESOLVED_BY_AGENT,
        "headline": "A courier asked where to leave a parcel",
        "started_at": call.started_at,
        "ended_at": call.ended_at,
    }
    fields.update(overrides)
    return CallSummary(**fields)


class Seed:
    """Commits calls the way orchestration will have left them."""

    def __init__(self, api: Api) -> None:
        self.api = api
        self.clock = FixedClock(NOW)

    @property
    def cipher(self) -> TranscriptCipher:
        cipher: TranscriptCipher = self.api.app.state.container.transcript_cipher
        return cipher

    async def call(
        self,
        call: CallSession,
        *,
        summary: CallSummary | None = None,
        said: list[TranscriptEntry] | None = None,
    ) -> None:
        async with unit_of_work(self.api.app.state.session_factory) as session:
            await SqlCallRepository(session, self.cipher, self.clock).save(call)
            if said:
                await SqlTranscriptRepository(session, self.cipher).append(
                    call.user_id, call.id, said
                )
            if summary is not None:
                await SqlSummaryRepository(session, self.cipher, self.clock).add(
                    call.user_id, summary
                )

    async def rows_for(self, call_id: str) -> dict[str, int]:
        async with unit_of_work(self.api.app.state.session_factory) as session:
            counts = {}
            for table, column in (
                ("calls", "id"),
                ("call_participants", "call_id"),
                ("call_transcript_entries", "call_id"),
                ("call_summaries", "call_id"),
            ):
                result = await session.execute(
                    text(f"SELECT count(*) FROM {table} WHERE {column} = :id"),  # noqa: S608
                    {"id": call_id},
                )
                counts[table] = int(result.scalar_one())
            return counts


def lines(*moments: datetime) -> list[TranscriptEntry]:
    return [
        TranscriptEntry(
            Speaker.CALLER if index % 2 == 0 else Speaker.AGENT, f"{SAID} {index}", moment
        )
        for index, moment in enumerate(moments)
    ]


async def listed(api: Api, who: Person, **params: Any) -> dict[str, Any]:
    response = await api.client.get("/v1/calls", headers=who.headers, params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def ids(page: dict[str, Any]) -> list[str]:
    return [item["id"] for item in page["calls"]]


def without_correlation(response: Response) -> tuple[int, dict[str, Any]]:
    body: dict[str, Any] = response.json()
    body.pop("correlation_id", None)
    return response.status_code, body


class TestListing:
    async def test_calls_are_newest_first_and_only_the_user_s_own(self, api: Api) -> None:
        me, them = await person(api), await person(api, ANOTHER_NUMBER)
        seed = Seed(api)
        for name, offset in (("oldest", 0), ("newest", 120), ("middle", 60)):
            call = a_call(me, name, started_at=at(offset))
            await seed.call(call, summary=a_summary(call))
        await seed.call(a_call(them, "theirs", started_at=at(90)))

        assert ids(await listed(api, me)) == ["newest", "middle", "oldest"]
        assert ids(await listed(api, them)) == ["theirs"]

    async def test_a_summarised_call_is_listed_with_what_happened(self, api: Api) -> None:
        me = await person(api)
        call = escalated_call(me)
        await Seed(api).call(
            call,
            summary=a_summary(
                call,
                outcome=CallOutcome.HANDED_TO_USER,
                human_joined_at=at(35),
                escalation_reason=REASON,
            ),
        )

        [item] = (await listed(api, me))["calls"]

        assert item == {
            "id": "escalated",
            "started_at": "2026-06-01T12:00:00Z",
            "status": "ended",
            "caller": {
                "category": "known_contact",
                "display_name": "Sam",
                "number_withheld": False,
            },
            "outcome": "handed_to_user",
            "headline": "A courier asked where to leave a parcel",
            "human_joined": True,
            "duration_seconds": 90.0,
        }

    async def test_a_call_in_progress_is_listed_with_no_outcome_yet(self, api: Api) -> None:
        # It rang the user's phone a moment ago; a history without it is missing a call they had.
        me = await person(api)
        await Seed(api).call(a_call(me, "ringing", state=CallState.AGENT_HANDLING))

        [item] = (await listed(api, me))["calls"]

        assert (item["status"], item["outcome"], item["headline"]) == ("in_progress", None, None)
        assert item["duration_seconds"] is None
        assert item["human_joined"] is False

    async def test_a_stranger_is_shown_by_category_never_by_name_or_number(self, api: Api) -> None:
        me = await person(api)
        withheld = a_call(me, "withheld", caller=Caller(), started_at=at(10))
        seed = Seed(api)
        await seed.call(a_call(me, "stranger"))
        await seed.call(withheld)

        response = await api.client.get("/v1/calls", headers=me.headers)

        assert [item["caller"] for item in response.json()["calls"]] == [
            {"category": "unknown", "display_name": None, "number_withheld": True},
            {"category": "sales", "display_name": None, "number_withheld": False},
        ]
        assert "Prize Desk" not in response.text
        assert STRANGER_NUMBER.value[-7:] not in response.text

    async def test_it_needs_a_token(self, api: Api) -> None:
        assert (await api.client.get("/v1/calls")).status_code == 401

    async def test_without_transcript_keys_history_is_unavailable_rather_than_broken(
        self, api: Api
    ) -> None:
        me = await person(api)
        container = api.app.state.container
        api.app.state.container = dataclasses.replace(container, transcript_cipher=None)

        response = await api.client.get("/v1/calls", headers=me.headers)

        assert response.status_code == 503
        assert response.json()["error"] == "call_history_unavailable"


class TestFilters:
    @pytest.fixture
    async def history(self, api: Api) -> Person:
        """Four ended calls a minute apart, one of each shape, and one still going."""
        me = await person(api)
        seed = Seed(api)
        handed = escalated_call(me, "handed")
        await seed.call(
            handed,
            summary=a_summary(
                handed,
                outcome=CallOutcome.HANDED_TO_USER,
                human_joined_at=at(35),
                escalation_reason=REASON,
            ),
        )
        for name, offset, outcome in (
            ("resolved", 60, CallOutcome.RESOLVED_BY_AGENT),
            ("rejected", 120, CallOutcome.REJECTED_BY_RULE),
            ("missed", 180, CallOutcome.UNANSWERED_ESCALATION),
        ):
            call = a_call(me, name, started_at=at(offset))
            await seed.call(call, summary=a_summary(call, outcome=outcome))
        await seed.call(a_call(me, "live", started_at=at(240), state=CallState.AGENT_HANDLING))
        return me

    async def test_by_outcome(self, api: Api, history: Person) -> None:
        assert ids(await listed(api, history, outcome="rejected_by_rule")) == ["rejected"]

    async def test_by_whether_the_user_joined(self, api: Api, history: Person) -> None:
        # A call with no summary yet matches neither: whether anybody joined it is not known.
        assert ids(await listed(api, history, human_joined="true")) == ["handed"]
        assert ids(await listed(api, history, human_joined="false")) == [
            "missed",
            "rejected",
            "resolved",
        ]

    async def test_by_date_from_inclusive_to_exclusive(self, api: Api, history: Person) -> None:
        page = await listed(api, history, **{"from": at(60).isoformat(), "to": at(180).isoformat()})
        assert ids(page) == ["rejected", "resolved"]

    async def test_a_date_in_another_zone_means_the_same_instant(
        self, api: Api, history: Person
    ) -> None:
        same_instant_five_hours_behind = "2026-06-01T07:03:00-05:00"
        assert ids(await listed(api, history, **{"from": same_instant_five_hours_behind})) == [
            "live",
            "missed",
        ]

    async def test_filters_combine(self, api: Api, history: Person) -> None:
        page = await listed(
            api, history, outcome="resolved_by_agent", human_joined="false", to=at(90).isoformat()
        )
        assert ids(page) == ["resolved"]

    @pytest.mark.parametrize(
        "params",
        [
            {"from": "2026-06-01T12:00:00"},
            {"to": "2026-06-01T12:00:00"},
            {"from": "2026-06-01T12:00:00Z", "to": "2026-06-01T12:00:00Z"},
            {"from": "2026-06-02T00:00:00Z", "to": "2026-06-01T00:00:00Z"},
            {"outcome": "went_well"},
            {"human_joined": "perhaps"},
        ],
        ids=["naive-from", "naive-to", "empty-range", "backwards", "outcome", "joined"],
    )
    async def test_a_malformed_filter_is_refused(
        self, api: Api, history: Person, params: dict[str, str]
    ) -> None:
        response = await api.client.get("/v1/calls", headers=history.headers, params=params)
        assert response.status_code == 422
        assert response.json()["error"] == "invalid_request"


class TestPagination:
    @pytest.fixture
    async def five_calls(self, api: Api) -> Person:
        """Five calls, three starting in the same instant, and somebody else's among them."""
        me, them = await person(api), await person(api, ANOTHER_NUMBER)
        seed = Seed(api)
        for name, offset in (("a", 0), ("b", 10), ("c", 10), ("d", 10), ("e", 20)):
            await seed.call(a_call(me, name, started_at=at(offset)))
        await seed.call(a_call(them, "theirs", started_at=at(10)))
        return me

    async def test_pages_walk_the_whole_history_exactly_once(
        self, api: Api, five_calls: Person
    ) -> None:
        seen: list[str] = []
        params: dict[str, Any] = {"limit": 2}
        pages = 0
        while True:
            page = await listed(api, five_calls, **params)
            seen.extend(ids(page))
            pages += 1
            if page["next_cursor"] is None:
                break
            params["cursor"] = page["next_cursor"]

        assert seen == ["e", "d", "c", "b", "a"]
        assert pages == 3

    async def test_a_cursor_continues_a_filtered_listing(
        self, api: Api, five_calls: Person
    ) -> None:
        window = {"from": at(10).isoformat(), "to": at(20).isoformat(), "limit": 2}
        first = await listed(api, five_calls, **window)
        rest = await listed(api, five_calls, **window, cursor=first["next_cursor"])
        assert (ids(first), ids(rest), rest["next_cursor"]) == (["d", "c"], ["b"], None)

    async def test_the_default_page_is_bounded(self, api: Api, five_calls: Person) -> None:
        assert len((await listed(api, five_calls))["calls"]) == 5
        assert (await listed(api, five_calls, limit=MAX_CALL_PAGE))["next_cursor"] is None

    @pytest.mark.parametrize("limit", [0, MAX_CALL_PAGE + 1, -1])
    async def test_a_limit_outside_the_bounds_is_refused(
        self, api: Api, five_calls: Person, limit: int
    ) -> None:
        response = await api.client.get(
            "/v1/calls", headers=five_calls.headers, params={"limit": limit}
        )
        assert response.status_code == 422
        assert response.json()["error"] == "invalid_request"

    @pytest.mark.parametrize(
        "cursor",
        [
            "not base64!",
            base64.urlsafe_b64encode(b"not json").decode(),
            base64.urlsafe_b64encode(b'{"a": 1}').decode(),
            base64.urlsafe_b64encode(b"[1, 2]").decode(),
            base64.urlsafe_b64encode(b'["yesterday", "a"]').decode(),
            base64.urlsafe_b64encode(b'["2026-06-01T12:00:00", "a"]').decode(),
            base64.urlsafe_b64encode(b'["2026-06-01T12:00:00+00:00", " a"]').decode(),
            base64.urlsafe_b64encode(b'["2026-06-01T12:00:00+00:00"]').decode(),
        ],
        ids=[
            "not-base64",
            "not-json",
            "an-object",
            "numbers",
            "not-a-time",
            "naive",
            "bad-id",
            "short",
        ],
    )
    async def test_a_tampered_cursor_is_refused(
        self, api: Api, five_calls: Person, cursor: str
    ) -> None:
        response = await api.client.get(
            "/v1/calls", headers=five_calls.headers, params={"cursor": cursor}
        )
        assert response.status_code == 422
        assert response.json()["error"] == "invalid_cursor"

    async def test_an_oversized_cursor_is_refused_before_it_is_decoded(
        self, api: Api, five_calls: Person
    ) -> None:
        response = await api.client.get(
            "/v1/calls", headers=five_calls.headers, params={"cursor": "A" * 10_000}
        )
        assert response.status_code == 422

    async def test_a_cursor_forged_from_somebody_else_s_call_still_pages_only_one_s_own(
        self, api: Api, five_calls: Person
    ) -> None:
        # All a cursor holds is a position in time; pointing it at another user's call moves
        # nothing but where the user's own list resumes.
        forged = base64.urlsafe_b64encode(
            json.dumps([at(10).isoformat(), "theirs"]).encode()
        ).decode()
        assert ids(await listed(api, five_calls, cursor=forged)) == ["d", "c", "b", "a"]


class TestDetail:
    async def test_an_escalated_call_in_full(self, api: Api) -> None:
        me = await person(api)
        call = escalated_call(me)
        await Seed(api).call(
            call,
            said=lines(at(3), at(30)),
            summary=a_summary(
                call,
                outcome=CallOutcome.HANDED_TO_USER,
                human_joined_at=at(35),
                escalation_reason=REASON,
                details=(
                    ExtractedDetail("reference", "ZX-9043", EVIDENCE),
                    ExtractedDetail("slot", "afternoon"),
                ),
            ),
        )

        response = await api.client.get("/v1/calls/escalated", headers=me.headers)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["timings"] == {
            "received_at": "2026-06-01T12:00:00Z",
            "answered_at": "2026-06-01T12:00:02Z",
            "escalated_at": "2026-06-01T12:00:30Z",
            "human_joined_at": "2026-06-01T12:00:35Z",
            "ended_at": "2026-06-01T12:01:30Z",
        }
        assert body["handling"] == "assistant"
        assert body["details"] == [
            {"label": "reference", "value": "ZX-9043", "evidence": EVIDENCE},
            {"label": "slot", "value": "afternoon", "evidence": None},
        ]
        assert (body["intent"], body["importance"], body["escalation_reason"]) == (
            "delivery_in_progress",
            40,
            "decision_needs_the_user",
        )
        assert (body["outcome"], body["human_joined"], body["caller"]["display_name"]) == (
            "handed_to_user",
            True,
            "Sam",
        )
        # The default retention, and the moment the last line said on the call is due to go.
        assert body["transcript_available"] is True
        assert body["transcript_retention_days"] == 7
        assert body["transcript_expires_at"] == "2026-06-08T12:01:30Z"

    async def test_the_retention_stated_is_the_user_s_own_setting(self, api: Api) -> None:
        me = await person(api)
        changed = await api.client.patch(
            "/v1/preferences",
            headers=me.headers,
            json={"privacy": {"transcript_retention_days": 30}},
        )
        assert changed.status_code == 200, changed.text
        call = a_call(me, "call")
        await Seed(api).call(call, said=lines(at(5)), summary=a_summary(call))

        body = (await api.client.get("/v1/calls/call", headers=me.headers)).json()

        assert body["transcript_retention_days"] == 30
        assert body["transcript_expires_at"] == "2026-07-01T12:01:00Z"

    async def test_a_rejected_call_was_never_answered_and_has_no_transcript(self, api: Api) -> None:
        me = await person(api)
        call = a_call(me, "rejected", state=CallState.REJECTED, lasting=1)
        await Seed(api).call(call, summary=fallback_summary(CallFacts(call), locale="en"))

        body = (await api.client.get("/v1/calls/rejected", headers=me.headers)).json()

        assert body["timings"]["answered_at"] is None
        assert body["timings"]["escalated_at"] is None
        assert body["timings"]["human_joined_at"] is None
        # Given to nobody: the rules refused it before anybody could take it.
        assert body["handling"] is None
        assert (body["transcript_available"], body["transcript_expires_at"]) == (False, None)
        assert body["headline"] == "A call from a sales caller was ended by your rules."
        assert body["details"] == []

    async def test_a_call_in_progress_has_no_end_and_no_expiry_yet(self, api: Api) -> None:
        me = await person(api)
        call = a_call(
            me,
            "live",
            state=CallState.HUMAN_JOINED,
            participants=(
                Participant(ParticipantRole.AGENT, at(1)),
                Participant(ParticipantRole.HUMAN, at(20)),
            ),
        )
        await Seed(api).call(call, said=lines(at(2)))

        body = (await api.client.get("/v1/calls/live", headers=me.headers)).json()

        assert (body["status"], body["outcome"], body["intent"]) == ("in_progress", None, None)
        assert body["timings"]["ended_at"] is None
        # Joined, read from who is on the call, before any summary says so.
        assert body["timings"]["human_joined_at"] == "2026-06-01T12:00:20Z"
        assert body["human_joined"] is True
        assert (body["transcript_available"], body["transcript_expires_at"]) == (True, None)

    @pytest.mark.parametrize("call_id", ["no-such-call", "%20padded"])
    async def test_a_call_that_does_not_exist_is_not_found(self, api: Api, call_id: str) -> None:
        me = await person(api)
        response = await api.client.get(f"/v1/calls/{call_id}", headers=me.headers)
        assert response.status_code == 404
        assert response.json()["error"] == "call_not_found"


class TestTranscript:
    async def test_a_retained_transcript_is_read_in_order_and_kept_out_of_caches(
        self, api: Api
    ) -> None:
        me = await person(api)
        call = a_call(me, "call")
        await Seed(api).call(call, said=lines(at(5), at(9)), summary=a_summary(call))

        response = await api.client.get("/v1/calls/call/transcript", headers=me.headers)

        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {
            "call_id": "call",
            "entries": [
                {"speaker": "caller", "text": f"{SAID} 0", "said_at": "2026-06-01T12:00:05Z"},
                {"speaker": "agent", "text": f"{SAID} 1", "said_at": "2026-06-01T12:00:09Z"},
            ],
            "transcript_retention_days": 7,
            "transcript_expires_at": "2026-06-08T12:01:00Z",
        }

    async def test_a_purged_transcript_is_gone_and_its_summary_still_stands(self, api: Api) -> None:
        me = await person(api)
        call = escalated_call(me)
        summary = fallback_summary(CallFacts(call, escalation_reason=REASON), locale="en")
        await Seed(api).call(call, said=lines(at(3), at(30)), summary=summary)

        # The real purge, run as the scheduler runs it, eight days on.
        result = await purge_transcripts(
            make_settings(),
            engine=api.app.state.engine,
            clock=FixedClock(NOW + timedelta(days=8)),
            metrics=RecordingMetrics(),
        )
        assert result.entries_deleted == 2

        transcript = await api.client.get("/v1/calls/escalated/transcript", headers=me.headers)
        assert transcript.status_code == 410
        assert transcript.json()["error"] == "transcript_purged"
        assert SAID not in transcript.text

        detail = (await api.client.get("/v1/calls/escalated", headers=me.headers)).json()
        assert (detail["transcript_available"], detail["transcript_expires_at"]) == (False, None)
        assert detail["headline"] == summary.headline
        assert (detail["outcome"], detail["timings"]["human_joined_at"]) == (
            "handed_to_user",
            "2026-06-01T12:00:35Z",
        )

    async def test_a_call_nothing_was_said_on_never_had_one(self, api: Api) -> None:
        me = await person(api)
        await Seed(api).call(a_call(me, "rejected", state=CallState.REJECTED))

        response = await api.client.get("/v1/calls/rejected/transcript", headers=me.headers)

        assert response.status_code == 404
        assert response.json()["error"] == "transcript_not_recorded"

    @pytest.mark.parametrize("call_id", ["no-such-call", "%20padded"])
    async def test_no_such_call_is_distinct_from_both(self, api: Api, call_id: str) -> None:
        me = await person(api)
        response = await api.client.get(f"/v1/calls/{call_id}/transcript", headers=me.headers)
        assert response.status_code == 404
        assert response.json()["error"] == "call_not_found"


class TestIsolation:
    @pytest.fixture
    async def their_call(self, api: Api) -> tuple[Person, Person]:
        me, them = await person(api), await person(api, ANOTHER_NUMBER)
        call = a_call(them, "theirs")
        await Seed(api).call(call, said=lines(at(5)), summary=a_summary(call))
        return me, them

    @pytest.mark.parametrize("path", ["/v1/calls/{}", "/v1/calls/{}/transcript"])
    async def test_somebody_else_s_call_answers_exactly_like_no_call(
        self, api: Api, their_call: tuple[Person, Person], path: str
    ) -> None:
        me, _ = their_call
        theirs = await api.client.get(path.format("theirs"), headers=me.headers)
        missing = await api.client.get(path.format("no-such-call"), headers=me.headers)

        assert without_correlation(theirs) == without_correlation(missing)
        assert theirs.status_code == 404
        assert SAID not in theirs.text

    async def test_somebody_else_s_call_is_not_listed(
        self, api: Api, their_call: tuple[Person, Person]
    ) -> None:
        me, _ = their_call
        assert (await listed(api, me)) == {"calls": [], "next_cursor": None}

    async def test_deleting_somebody_else_s_call_answers_like_success_and_deletes_nothing(
        self, api: Api, their_call: tuple[Person, Person]
    ) -> None:
        me, them = their_call

        response = await api.client.delete("/v1/calls/theirs", headers=me.headers)

        assert response.status_code == 204
        assert (await Seed(api).rows_for("theirs"))["call_transcript_entries"] == 1
        assert (await api.client.get("/v1/calls/theirs", headers=them.headers)).status_code == 200


class TestDeletion:
    async def test_everything_about_the_call_goes_at_once(self, api: Api) -> None:
        me = await person(api)
        seed = Seed(api)
        call = escalated_call(me)
        kept = a_call(me, "kept", started_at=at(300))
        await seed.call(
            call,
            said=lines(at(3), at(30)),
            summary=a_summary(
                call,
                outcome=CallOutcome.HANDED_TO_USER,
                human_joined_at=at(35),
                escalation_reason=REASON,
            ),
        )
        await seed.call(kept, said=lines(at(301)), summary=a_summary(kept))
        assert all(await seed.rows_for("escalated"))

        response = await api.client.delete("/v1/calls/escalated", headers=me.headers)

        assert response.status_code == 204
        assert response.content == b""
        assert await seed.rows_for("escalated") == {
            "calls": 0,
            "call_participants": 0,
            "call_transcript_entries": 0,
            "call_summaries": 0,
        }
        assert (await api.client.get("/v1/calls/escalated", headers=me.headers)).status_code == 404
        assert ids(await listed(api, me)) == ["kept"]
        assert await seed.rows_for("kept") == {
            "calls": 1,
            "call_participants": 0,
            "call_transcript_entries": 1,
            "call_summaries": 1,
        }

    async def test_what_the_user_was_told_about_the_call_goes_with_it(self, api: Api) -> None:
        # The escalation context repeats who called and what they wanted in words of its own. A
        # call deleted with that left behind is a call the user was told is gone and is not.
        me = await person(api)
        call = escalated_call(me)
        await Seed(api).call(call)
        async with unit_of_work(api.app.state.session_factory) as session:
            assert await SqlEscalationContextRepository(session).claim(
                me.user_id,
                EscalationContext(
                    call_id=call.id,
                    reason=REASON,
                    raised_at=at(30),
                    caller_label="a courier",
                    established="They are at the gate.",
                    needed="Where to leave the parcel.",
                ),
            )
        shown = f"/v1/escalations/{call.id.value}"
        assert (await api.client.get(shown, headers=me.headers)).status_code == 200

        await api.client.delete(f"/v1/calls/{call.id.value}", headers=me.headers)

        assert (await api.client.get(shown, headers=me.headers)).status_code == 404

    @pytest.mark.parametrize("call_id", ["escalated", "no-such-call", "%20padded"])
    async def test_deleting_again_or_deleting_nothing_succeeds(
        self, api: Api, call_id: str
    ) -> None:
        me = await person(api)
        await Seed(api).call(escalated_call(me))

        first = await api.client.delete(f"/v1/calls/{call_id}", headers=me.headers)
        second = await api.client.delete(f"/v1/calls/{call_id}", headers=me.headers)

        assert (first.status_code, second.status_code) == (204, 204)

    async def test_it_needs_a_token(self, api: Api) -> None:
        assert (await api.client.delete("/v1/calls/anything")).status_code == 401
