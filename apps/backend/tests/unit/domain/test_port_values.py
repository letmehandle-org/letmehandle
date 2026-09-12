"""The value objects the ports pass around, and the states they refuse to be in.

These guards are the contract as much as the method signatures are. A transport that declares
an impossible combination, or a notification with nothing in it, are both failures that would
otherwise surface while somebody is on the phone.
"""

from __future__ import annotations

import pytest

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.identifiers import CallId
from letmehandle.domain.ports.call_transport import TransportCapabilities
from letmehandle.domain.ports.notification import (
    DevicePlatform,
    DeviceToken,
    EscalationNotification,
)
from letmehandle.domain.ports.speech import SpeechCapabilities, TranscriptProduced
from letmehandle.domain.ports.voice import Voice, VoiceSample


class TestTransportCapabilities:
    def test_everything_defaults_to_false(self) -> None:
        # A transport that forgets to declare something offers less than it could, which is
        # recoverable. One that inherits a true it did not mean promises a caller something
        # that fails while they are on the line.
        capabilities = TransportCapabilities()
        assert not any(capabilities.has(name) for name in capabilities.names())

    def test_injecting_audio_without_hearing_the_caller_is_refused(self) -> None:
        # It would be an announcement, not a conversation.
        with pytest.raises(InvariantError, match="without listening"):
            TransportCapabilities(can_inject_ai_audio=True)

    def test_a_three_way_call_without_a_way_to_add_the_third_party_is_refused(self) -> None:
        with pytest.raises(InvariantError, match="third party"):
            TransportCapabilities(supports_three_way_call=True)

    def test_agent_conversation_needs_both_directions(self) -> None:
        # Asked as one question so that two flags cannot be checked together in some places
        # and separately in others.
        assert not TransportCapabilities(
            can_stream_call_audio_to_ai=True
        ).supports_agent_conversation
        assert TransportCapabilities(
            can_stream_call_audio_to_ai=True, can_inject_ai_audio=True
        ).supports_agent_conversation

    def test_asking_about_a_capability_that_does_not_exist_is_an_error(self) -> None:
        # A typo would otherwise read as "not supported", and the feature would quietly
        # disappear from the product.
        with pytest.raises(InvariantError, match="not a transport capability"):
            TransportCapabilities().has("can_read_minds")

    def test_the_capability_names_are_the_documented_matrix(self) -> None:
        assert set(TransportCapabilities().names()) == {
            "can_screen_before_ringing",
            "can_stream_call_audio_to_ai",
            "can_inject_ai_audio",
            "can_bridge_human",
            "supports_three_way_call",
            "supports_native_ringing",
        }


class TestNotificationValues:
    def test_an_empty_device_token_is_refused(self) -> None:
        with pytest.raises(InvariantError):
            DeviceToken(DevicePlatform.IOS, "  ")

    def test_a_token_is_truncated_when_rendered(self) -> None:
        # A device token identifies somebody's handset.
        rendered = str(DeviceToken(DevicePlatform.IOS, "abcdefghijklmnop"))
        assert "abcdef" in rendered
        assert "ghijklmnop" not in rendered

    @pytest.mark.parametrize(("title", "body"), [("", "b"), ("t", ""), ("  ", "b")])
    def test_a_notification_with_nothing_in_it_is_refused(self, title: str, body: str) -> None:
        # Worse than sending none: it interrupts and explains nothing.
        with pytest.raises(InvariantError):
            EscalationNotification(
                call_id=CallId("c"), title=title, body=body, caller_label="someone"
            )


class TestSpeechValues:
    def test_a_fragment_says_who_spoke_and_whether_it_is_settled(self) -> None:
        # Acting on a partial recognition is how an assistant answers a question the caller
        # had not finished asking.
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
        # It could never be selected for any user, so it is a catalogue entry that does
        # nothing but confuse the person reading the list.
        with pytest.raises(InvariantError, match="no language"):
            Voice(id="calm", name="Calm", locales=())

    def test_a_voice_listed_for_a_language_serves_its_regional_variants(self) -> None:
        voice = Voice(id="calm", name="Calm", locales=("en",))
        assert voice.speaks("en")
        assert voice.speaks("en-GB")
        assert not voice.speaks("fr")
        assert not voice.speaks("english")

    def test_a_sample_with_no_audio_is_refused(self) -> None:
        # A preview control that plays nothing is worse than no control: it reads as the
        # product being broken rather than as a feature this deployment does not have.
        with pytest.raises(InvariantError, match="silence"):
            VoiceSample(audio=b"", media_type="audio/mpeg")

    def test_a_sample_must_say_what_format_it_is_in(self) -> None:
        # A caller that has to guess gets it wrong the first time a provider returns anything
        # other than the format that was assumed, and the symptom is silence again.
        with pytest.raises(InvariantError, match="what format"):
            VoiceSample(audio=b"audio", media_type="  ")
