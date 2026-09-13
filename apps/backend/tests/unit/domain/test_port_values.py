"""The value objects the ports pass around, and the states they refuse."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.identifiers import CallId, EventId
from letmehandle.domain.ports.call_transport import (
    CallEvent,
    CallEventKind,
    ParticipantOutcome,
    ParticipantRole,
    ScreeningDecision,
    TransportCapabilities,
)
from letmehandle.domain.ports.notification import (
    DevicePlatform,
    DeviceToken,
    EscalationNotification,
)
from letmehandle.domain.ports.repositories import MAX_CALL_PAGE, CallFilter, check_page_size
from letmehandle.domain.ports.speech import SpeechCapabilities, TranscriptProduced
from letmehandle.domain.ports.voice import Voice, VoiceSample


class TestTransportCapabilities:
    def test_everything_defaults_to_false(self) -> None:
        capabilities = TransportCapabilities()
        assert not any(capabilities.has(name) for name in capabilities.names())

    def test_injecting_audio_without_hearing_the_caller_is_refused(self) -> None:
        with pytest.raises(InvariantError, match="without listening"):
            TransportCapabilities(can_inject_ai_audio=True)

    def test_a_three_way_call_without_a_way_to_add_the_third_party_is_refused(self) -> None:
        with pytest.raises(InvariantError, match="third party"):
            TransportCapabilities(supports_three_way_call=True)

    def test_agent_conversation_needs_both_directions(self) -> None:
        assert not TransportCapabilities(
            can_stream_call_audio_to_ai=True
        ).supports_agent_conversation
        assert TransportCapabilities(
            can_stream_call_audio_to_ai=True, can_inject_ai_audio=True
        ).supports_agent_conversation

    def test_asking_about_a_capability_that_does_not_exist_is_an_error(self) -> None:
        with pytest.raises(InvariantError, match="not a transport capability"):
            TransportCapabilities().has("can_read_minds")

    def test_the_capability_names_are_the_documented_matrix(self) -> None:
        assert set(TransportCapabilities().names()) == {
            "can_answer_under_program_control",
            "can_screen_before_ringing",
            "can_stream_call_audio_to_ai",
            "can_inject_ai_audio",
            "can_bridge_human",
            "supports_three_way_call",
            "supports_native_ringing",
        }


class TestCallEvents:
    """The shapes an event may take."""

    @staticmethod
    def event(
        kind: CallEventKind,
        participant: ParticipantRole | None = None,
        outcome: ParticipantOutcome | None = None,
    ) -> CallEvent:
        return CallEvent(kind, CallId("c"), EventId("e"), participant=participant, outcome=outcome)

    def test_a_participant_event_says_whom_it_is_about(self) -> None:
        with pytest.raises(InvariantError, match="which participant"):
            self.event(CallEventKind.PARTICIPANT_LEFT)

    def test_an_event_about_the_whole_call_names_no_participant(self) -> None:
        with pytest.raises(InvariantError, match="which participant"):
            self.event(CallEventKind.ENDED, ParticipantRole.USER)

    @pytest.mark.parametrize("outcome", [None, ParticipantOutcome.ANSWERED])
    def test_an_unreachable_participant_says_why(self, outcome: ParticipantOutcome | None) -> None:
        with pytest.raises(InvariantError, match="outcome other than answered"):
            self.event(CallEventKind.PARTICIPANT_UNREACHABLE, ParticipantRole.USER, outcome)

    def test_leaving_has_no_dialling_outcome(self) -> None:
        with pytest.raises(InvariantError, match="dialling outcome"):
            self.event(
                CallEventKind.PARTICIPANT_LEFT, ParticipantRole.USER, ParticipantOutcome.BUSY
            )

    def test_a_participant_who_joined_was_answered(self) -> None:
        with pytest.raises(InvariantError, match="was answered"):
            self.event(
                CallEventKind.PARTICIPANT_JOINED, ParticipantRole.USER, ParticipantOutcome.BUSY
            )

    def test_only_a_person_can_be_answered_by_a_machine(self) -> None:
        with pytest.raises(InvariantError, match="machine"):
            self.event(
                CallEventKind.PARTICIPANT_UNREACHABLE,
                ParticipantRole.ASSISTANT,
                ParticipantOutcome.ANSWERED_BY_MACHINE,
            )

    def test_the_shapes_that_are_allowed(self) -> None:
        self.event(CallEventKind.INCOMING)
        self.event(CallEventKind.PARTICIPANT_JOINED, ParticipantRole.ASSISTANT)
        self.event(
            CallEventKind.PARTICIPANT_JOINED, ParticipantRole.USER, ParticipantOutcome.ANSWERED
        )
        unreachable = self.event(
            CallEventKind.PARTICIPANT_UNREACHABLE,
            ParticipantRole.USER,
            ParticipantOutcome.ANSWERED_BY_MACHINE,
        )
        assert unreachable.outcome is ParticipantOutcome.ANSWERED_BY_MACHINE

    def test_the_incoming_event_carries_the_screening_decision(self) -> None:
        event = CallEvent(
            CallEventKind.INCOMING, CallId("c"), EventId("e"), screening=ScreeningDecision.REJECT
        )
        assert event.screening is ScreeningDecision.REJECT

    @pytest.mark.parametrize(
        "kind", [kind for kind in CallEventKind if kind is not CallEventKind.INCOMING]
    )
    def test_no_later_event_carries_one(self, kind: CallEventKind) -> None:
        with pytest.raises(InvariantError, match="incoming event only"):
            CallEvent(kind, CallId("c"), EventId("e"), screening=ScreeningDecision.ALLOW)

    def test_an_event_reported_after_the_fact_says_when_it_happened(self) -> None:
        at = datetime(2026, 9, 13, 12, 11, 33, tzinfo=UTC)
        event = CallEvent(CallEventKind.ENDED, CallId("c"), EventId("e"), occurred_at=at)
        assert event.occurred_at == at
        assert CallEvent(CallEventKind.ENDED, CallId("c"), EventId("e")).occurred_at is None

    def test_a_moment_without_a_timezone_is_refused(self) -> None:
        with pytest.raises(InvariantError, match="timezone"):
            CallEvent(
                CallEventKind.ENDED,
                CallId("c"),
                EventId("e"),
                occurred_at=datetime(2026, 9, 13, 12, 11, 33),
            )


class TestNotificationValues:
    def test_an_empty_device_token_is_refused(self) -> None:
        with pytest.raises(InvariantError):
            DeviceToken(DevicePlatform.IOS, "  ")

    def test_a_token_is_truncated_when_rendered(self) -> None:
        rendered = str(DeviceToken(DevicePlatform.IOS, "abcdefghijklmnop"))
        assert "abcdef" in rendered
        assert "ghijklmnop" not in rendered

    @pytest.mark.parametrize(("title", "body"), [("", "b"), ("t", ""), ("  ", "b")])
    def test_a_notification_with_nothing_in_it_is_refused(self, title: str, body: str) -> None:
        with pytest.raises(InvariantError):
            EscalationNotification(
                call_id=CallId("c"), title=title, body=body, caller_label="someone"
            )


class TestSpeechValues:
    def test_a_fragment_says_who_spoke_and_whether_it_is_settled(self) -> None:
        partial = TranscriptProduced(text="I have a", speaker_is_caller=True, is_final=False)
        assert partial.speaker_is_caller
        assert not partial.is_final

    def test_an_empty_transcript_fragment_is_refused(self) -> None:
        with pytest.raises(InvariantError):
            TranscriptProduced(text="  ", speaker_is_caller=True, is_final=True)

    def test_language_matching_ignores_the_regional_tag(self) -> None:
        capabilities = SpeechCapabilities(languages=("en",))
        assert capabilities.speaks("en")
        assert capabilities.speaks("en-GB")
        assert not capabilities.speaks("fr")


class TestVoiceValues:
    @pytest.mark.parametrize(("voice_id", "name"), [("", "Calm"), ("calm", "  ")])
    def test_a_voice_needs_an_identifier_and_a_name(self, voice_id: str, name: str) -> None:
        with pytest.raises(InvariantError):
            Voice(id=voice_id, name=name, locales=("en",))

    def test_a_voice_that_speaks_nothing_is_refused(self) -> None:
        with pytest.raises(InvariantError, match="no language"):
            Voice(id="calm", name="Calm", locales=())

    def test_a_voice_listed_for_a_language_serves_its_regional_variants(self) -> None:
        voice = Voice(id="calm", name="Calm", locales=("en",))
        assert voice.speaks("en")
        assert voice.speaks("en-GB")
        assert not voice.speaks("fr")
        assert not voice.speaks("english")

    def test_a_sample_with_no_audio_is_refused(self) -> None:
        with pytest.raises(InvariantError, match="silence"):
            VoiceSample(audio=b"", media_type="audio/mpeg")

    def test_a_sample_must_say_what_format_it_is_in(self) -> None:
        with pytest.raises(InvariantError, match="what format"):
            VoiceSample(audio=b"audio", media_type="  ")


class TestPageSize:
    @pytest.mark.parametrize("limit", [1, MAX_CALL_PAGE])
    def test_a_size_within_bounds_is_accepted(self, limit: int) -> None:
        assert check_page_size(limit, MAX_CALL_PAGE) == limit

    @pytest.mark.parametrize("limit", [0, -1, MAX_CALL_PAGE + 1])
    def test_a_size_outside_them_is_refused_rather_than_clamped(self, limit: int) -> None:
        with pytest.raises(InvariantError):
            check_page_size(limit, MAX_CALL_PAGE)


class TestCallFilter:
    def test_an_open_ended_range_is_allowed(self) -> None:
        start = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        assert CallFilter(started_from=start).started_before is None
        assert CallFilter(started_before=start).started_from is None

    def test_a_bound_with_no_timezone_is_refused(self) -> None:
        naive = datetime(2026, 6, 1, 12, 0)
        with pytest.raises(InvariantError, match="timezone"):
            CallFilter(started_from=naive)
        with pytest.raises(InvariantError, match="timezone"):
            CallFilter(started_before=naive)

    @pytest.mark.parametrize("gap", [timedelta(0), timedelta(seconds=-1)])
    def test_a_range_that_does_not_move_forward_is_refused(self, gap: timedelta) -> None:
        start = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        with pytest.raises(InvariantError, match="starts before it ends"):
            CallFilter(started_from=start, started_before=start + gap)
