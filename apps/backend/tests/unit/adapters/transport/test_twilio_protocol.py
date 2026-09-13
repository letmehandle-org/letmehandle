"""The provider's three vocabularies: instructions out, media messages both ways, callbacks in."""

from __future__ import annotations

import base64
import json
from xml.etree.ElementTree import fromstring

import pytest

from letmehandle.adapters.transport.twilio import twiml
from letmehandle.adapters.transport.twilio.callbacks import (
    CallbackMalformedError,
    ConferenceEvent,
    LegStatus,
    Parameters,
    read_conference_update,
    read_incoming_call,
    read_leg_progress,
)
from letmehandle.adapters.transport.twilio.media import (
    Connected,
    DigitPressed,
    MarkReached,
    MediaProtocolError,
    MediaReceived,
    StreamStarted,
    StreamStopped,
    UnknownMessage,
    clear_message,
    mark_message,
    media_message,
    parse_message,
)


def parse(document: str):  # type: ignore[no-untyped-def]
    return fromstring(document)  # noqa: S314 - written by the code under test


# ------------------------------------------------------------------------ instructions


class TestInstructions:
    def test_the_caller_is_answered_into_a_conference_shaped_for_d027(self) -> None:
        root = parse(
            twiml.caller_conference(
                conference_name="call-CAsim-1",
                status_callback_url="https://calls.example.com/status?call=CAsim-1&x=1",
                participant_label="caller",
                dial_action_url="https://calls.example.com/left?call=CAsim-1",
            )
        )
        dial = root.find("./Dial")
        assert dial is not None
        # The caller's own leg says when their time in the conference is over.
        assert dial.attrib == {
            "action": "https://calls.example.com/left?call=CAsim-1",
            "method": "POST",
        }
        conference = root.find("./Dial/Conference")
        assert conference is not None
        assert conference.text == "call-CAsim-1"
        attributes = conference.attrib
        # Ending the conference when the caller leaves; no beep; silence while waiting; the
        # smallest mixer buffer; never recorded; and told about every join and leave.
        assert attributes["endConferenceOnExit"] == "true"
        assert attributes["startConferenceOnEnter"] == "true"
        assert attributes["beep"] == "false"
        assert attributes["waitUrl"] == ""
        assert attributes["jitterBufferSize"] == "small"
        assert attributes["record"] == "do-not-record"
        assert attributes["participantLabel"] == "caller"
        assert attributes["statusCallbackEvent"] == "start end join leave"
        assert attributes["statusCallback"].endswith("?call=CAsim-1&x=1")

    def test_values_are_escaped_rather_than_breaking_the_document(self) -> None:
        document = twiml.assistant_stream(
            stream_url="wss://calls.example.com/media",
            parameters={"call": 'a&b<"c">', "leg": "assistant-1"},
        )
        assert "&amp;" in document
        stream = parse(document).find("./Connect/Stream")
        assert stream is not None
        assert stream.attrib["url"] == "wss://calls.example.com/media"
        values = {each.attrib["name"]: each.attrib["value"] for each in stream}
        assert values == {"call": 'a&b<"c">', "leg": "assistant-1"}

    def test_hanging_up_is_a_document_of_its_own(self) -> None:
        assert parse(twiml.hang_up()).find("./Hangup") is not None


# ----------------------------------------------------------------------------- media

START = {
    "event": "start",
    "sequenceNumber": "1",
    "streamSid": "MZsim-1",
    "start": {
        "accountSid": "account-simulated",
        "streamSid": "MZsim-1",
        "callSid": "CAsim-assistant",
        "tracks": ["inbound"],
        "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
        "customParameters": {"call": "CAsim-1", "leg": "assistant-1"},
    },
}


def text(message: object) -> str:
    return json.dumps(message)


class TestMediaMessages:
    def test_connected(self) -> None:
        assert parse_message(text({"event": "connected", "protocol": "Call"})) == Connected()

    def test_start_carries_the_call_the_leg_and_the_parameters(self) -> None:
        started = parse_message(text(START))
        assert started == StreamStarted(
            stream_sid="MZsim-1",
            call_sid="CAsim-assistant",
            tracks=("inbound",),
            parameters={"call": "CAsim-1", "leg": "assistant-1"},
        )

    def test_start_takes_the_stream_identifier_from_the_top_level_when_the_section_has_none(
        self,
    ) -> None:
        start = json.loads(text(START))
        del start["start"]["streamSid"]
        started = parse_message(text(start))
        assert isinstance(started, StreamStarted)
        assert started.stream_sid == "MZsim-1"

    def test_numbers_written_as_strings_are_read_as_numbers(self) -> None:
        message = parse_message(
            text(
                {
                    "event": "media",
                    "sequenceNumber": "3",
                    "media": {
                        "track": "inbound",
                        "chunk": "2",
                        "timestamp": "40",
                        "payload": base64.b64encode(b"\x00\xff").decode(),
                    },
                }
            )
        )
        assert message == MediaReceived(
            track="inbound", chunk=2, timestamp_ms=40, payload=b"\x00\xff"
        )

    def test_numbers_written_as_numbers_are_read_too(self) -> None:
        message = parse_message(
            text(
                {
                    "event": "media",
                    "media": {"track": "inbound", "chunk": 2, "timestamp": 40, "payload": ""},
                }
            )
        )
        assert isinstance(message, MediaReceived)
        assert message.chunk == 2

    def test_mark_dtmf_stop_and_an_event_nobody_has_seen_before(self) -> None:
        assert parse_message(text({"event": "mark", "mark": {"name": "m1"}})) == MarkReached("m1")
        assert parse_message(
            text({"event": "dtmf", "dtmf": {"track": "inbound_track", "digit": "5"}})
        ) == DigitPressed("5")
        assert parse_message(text({"event": "stop", "stop": {}})) == StreamStopped()
        assert parse_message(text({"event": "brand-new"})) == UnknownMessage("brand-new")

    @pytest.mark.parametrize(
        ("frame", "reason"),
        [
            ("not json", "not JSON"),
            ("[1]", "not a JSON object"),
            ('{"sequenceNumber": "1"}', "names no event"),
            ('{"event": "mark"}', "no mark section"),
            ('{"event": "mark", "mark": {"name": 3}}', "name is missing"),
            (
                '{"event": "media", "media": {"track": "inbound", "chunk": "1", '
                '"timestamp": "1", "payload": "@@@"}}',
                "not base64",
            ),
            (
                '{"event": "media", "media": {"track": "inbound", "chunk": "x", '
                '"timestamp": "1", "payload": ""}}',
                "chunk is not a number",
            ),
            (
                '{"event": "media", "media": {"track": "inbound", "chunk": true, '
                '"timestamp": "1", "payload": ""}}',
                "chunk is not a number",
            ),
        ],
    )
    def test_a_frame_that_is_not_the_protocol_is_refused_without_repeating_it(
        self, frame: str, reason: str
    ) -> None:
        with pytest.raises(MediaProtocolError, match=reason) as failure:
            parse_message(frame)
        assert "@@@" not in str(failure.value)

    @pytest.mark.parametrize(
        ("change", "reason"),
        [
            (lambda start: start.pop("mediaFormat"), "no audio format"),
            (
                lambda start: start["mediaFormat"].update(encoding="audio/l16"),
                "not mono μ-law",
            ),
            (lambda start: start["mediaFormat"].update(sampleRate=16000), "not mono μ-law"),
            (lambda start: start.update(tracks="inbound"), "tracks"),
            (lambda start: start.update(customParameters={"call": 1}), "parameters"),
            (lambda start: start.update(customParameters=["call"]), "parameters"),
        ],
    )
    def test_a_start_this_transport_cannot_use_is_refused(
        self, change: object, reason: str
    ) -> None:
        start = json.loads(text(START))
        change(start["start"])  # type: ignore[operator]
        with pytest.raises(MediaProtocolError, match=reason):
            parse_message(text(start))

    def test_a_start_with_no_stream_identifier_is_refused(self) -> None:
        start = json.loads(text(START))
        del start["start"]["streamSid"]
        del start["streamSid"]
        with pytest.raises(MediaProtocolError, match="no identifier"):
            parse_message(text(start))

    def test_outgoing_messages_are_the_documented_shapes(self) -> None:
        assert json.loads(media_message("MZsim-1", b"\x7f\x80")) == {
            "event": "media",
            "streamSid": "MZsim-1",
            "media": {"payload": base64.b64encode(b"\x7f\x80").decode()},
        }
        assert json.loads(clear_message("MZsim-1")) == {"event": "clear", "streamSid": "MZsim-1"}
        assert json.loads(mark_message("MZsim-1", "end")) == {
            "event": "mark",
            "streamSid": "MZsim-1",
            "mark": {"name": "end"},
        }


# ------------------------------------------------------------------------- callbacks


class TestCallbacks:
    def test_an_incoming_call(self) -> None:
        call = read_incoming_call(
            Parameters(
                [
                    ("CallSid", "CAsim-1"),
                    ("AccountSid", "acct"),
                    ("From", "+12025550123"),
                    ("To", ""),
                ]
            )
        )
        assert (call.call_sid, call.account_sid, call.caller, call.called) == (
            "CAsim-1",
            "acct",
            "+12025550123",
            None,
        )

    def test_a_conference_update(self) -> None:
        update = read_conference_update(
            Parameters(
                [
                    ("ConferenceSid", "CFsim-1"),
                    ("AccountSid", "acct"),
                    ("StatusCallbackEvent", "participant-join"),
                    ("SequenceNumber", "4"),
                    ("CallSid", "CAsim-2"),
                    ("ParticipantLabel", "user-2"),
                ]
            )
        )
        assert update.event is ConferenceEvent.JOIN
        assert update.sequence == 4
        assert (update.call_sid, update.label, update.reason) == ("CAsim-2", "user-2", None)

    def test_a_conference_event_this_transport_does_not_act_on_is_read_as_none(self) -> None:
        update = read_conference_update(
            Parameters(
                [
                    ("ConferenceSid", "CFsim-1"),
                    ("AccountSid", "acct"),
                    ("StatusCallbackEvent", "participant-mute"),
                    ("SequenceNumber", "5"),
                ]
            )
        )
        assert update.event is None

    @pytest.mark.parametrize(
        ("params", "reason"),
        [
            (
                [("AccountSid", "a"), ("StatusCallbackEvent", "x"), ("SequenceNumber", "1")],
                "ConferenceSid",
            ),
            (
                [
                    ("ConferenceSid", "c"),
                    ("AccountSid", "a"),
                    ("StatusCallbackEvent", "x"),
                    ("SequenceNumber", "one"),
                ],
                "not a number",
            ),
        ],
    )
    def test_a_malformed_conference_update_is_refused(
        self, params: list[tuple[str, str]], reason: str
    ) -> None:
        with pytest.raises(CallbackMalformedError, match=reason):
            read_conference_update(Parameters(params))

    @pytest.mark.parametrize(
        ("answered_by", "machine"),
        [
            (None, False),
            ("human", False),
            ("unknown", False),
            ("machine_start", True),
            ("machine_end_beep", True),
            ("fax", True),
        ],
    )
    def test_leg_progress_and_who_answered(self, answered_by: str | None, machine: bool) -> None:
        pairs = [("CallSid", "CAsim-3"), ("AccountSid", "a"), ("CallStatus", "in-progress")]
        if answered_by is not None:
            pairs.append(("AnsweredBy", answered_by))
        progress = read_leg_progress(Parameters(pairs))
        assert progress.status is LegStatus.IN_PROGRESS
        assert progress.sequence is None
        assert progress.answered_by_machine is machine

    def test_a_status_this_transport_does_not_know_is_read_as_none(self) -> None:
        progress = read_leg_progress(
            Parameters(
                [
                    ("CallSid", "c"),
                    ("AccountSid", "a"),
                    ("CallStatus", "something-new"),
                    ("SequenceNumber", "2"),
                ]
            )
        )
        assert progress.status is None
        assert progress.sequence == 2

    def test_malformed_leg_progress_is_refused(self) -> None:
        with pytest.raises(CallbackMalformedError, match="not a number"):
            read_leg_progress(
                Parameters(
                    [
                        ("CallSid", "c"),
                        ("AccountSid", "a"),
                        ("CallStatus", "ringing"),
                        ("SequenceNumber", "-1"),
                    ]
                )
            )

    def test_which_statuses_are_final(self) -> None:
        final = {status for status in LegStatus if status.is_final}
        assert final == {
            LegStatus.COMPLETED,
            LegStatus.BUSY,
            LegStatus.NO_ANSWER,
            LegStatus.FAILED,
            LegStatus.CANCELED,
        }
