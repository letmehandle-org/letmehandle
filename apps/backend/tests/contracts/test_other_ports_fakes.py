"""The remaining contracts, run against the in-memory implementations."""

from __future__ import annotations

import pytest

from letmehandle.domain.models.identifiers import CallId
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.voice import VoiceSelection
from letmehandle.domain.ports.notification import (
    DeliveryStatus,
    DevicePlatform,
    DeviceToken,
    EscalationNotification,
)
from letmehandle.domain.ports.voice import resolve_voice
from tests.contracts.fakes import (
    CheckingOTPProvider,
    CountingIdGenerator,
    FixedClock,
    RecordingNotificationProvider,
    RecordingOTPProvider,
    StaticVoiceProvider,
)
from tests.contracts.other_ports import (
    ClockContract,
    IdGeneratorContract,
    NotificationProviderContract,
    OTPProviderContract,
    VoiceProviderContract,
)


class TestFixedClock(ClockContract):
    @pytest.fixture
    def clock(self) -> FixedClock:
        return FixedClock()

    def test_it_moves_only_when_told_to(self, clock: FixedClock) -> None:
        before = clock.now()
        clock.advance(60)
        assert (clock.now() - before).total_seconds() == 60


class TestCountingIdGenerator(IdGeneratorContract):
    @pytest.fixture
    def generator(self) -> CountingIdGenerator:
        return CountingIdGenerator()

    def test_identifiers_are_predictable(self, generator: CountingIdGenerator) -> None:
        assert generator.generate() == "id-1"
        assert generator.generate() == "id-2"


class TestRecordingOTPProvider(OTPProviderContract):
    @pytest.fixture
    def otp(self) -> RecordingOTPProvider:
        return RecordingOTPProvider()

    def test_it_admits_that_it_is_not_for_production(self, otp: RecordingOTPProvider) -> None:
        assert not otp.is_safe_for_production

    async def test_it_remembers_what_it_was_asked_to_send(self, otp: RecordingOTPProvider) -> None:
        number = PhoneNumber.parse("+12025550143")
        await otp.send(number, "123456")
        assert otp.sent == [(number, "123456")]


class TestCheckingOTPProvider(OTPProviderContract):
    @pytest.fixture
    def otp(self) -> CheckingOTPProvider:
        return CheckingOTPProvider()

    async def test_the_code_it_sent_is_accepted_once(self, otp: CheckingOTPProvider) -> None:
        number = PhoneNumber.parse("+12025550143")
        await otp.send_own_code(number)
        assert otp.sent == []
        assert await otp.check(number, "000000") is False
        assert await otp.check(number, otp.code) is True
        assert await otp.check(number, otp.code) is False


class TestRecordingNotificationProvider(NotificationProviderContract):
    @pytest.fixture
    def notifier(self) -> RecordingNotificationProvider:
        return RecordingNotificationProvider()

    @pytest.fixture
    def call_id(self) -> CallId:
        return CallId("call-1")

    async def test_a_failure_is_reported_rather_than_raised(self) -> None:
        # A failed push degrades the experience and never cancels the call already ringing.
        notifier = RecordingNotificationProvider(status=DeliveryStatus.FAILED)
        outcome = await notifier.send(
            DeviceToken(DevicePlatform.IOS, "t"),
            EscalationNotification(
                call_id=CallId("c"), title="t", body="b", caller_label="someone"
            ),
        )
        assert not outcome.succeeded
        assert not outcome.token_should_be_removed

    async def test_a_dead_token_is_reported_as_one_to_remove(self) -> None:
        notifier = RecordingNotificationProvider(status=DeliveryStatus.TOKEN_INVALID)
        outcome = await notifier.send(
            DeviceToken(DevicePlatform.IOS, "t"),
            EscalationNotification(
                call_id=CallId("c"), title="t", body="b", caller_label="someone"
            ),
        )
        assert outcome.token_should_be_removed

    async def test_a_token_for_the_wrong_platform_is_a_defect_and_raises(self) -> None:
        # A token for the wrong platform is a caller bug, so it raises instead of being reported.
        notifier = RecordingNotificationProvider(platform=DevicePlatform.IOS)
        with pytest.raises(Exception, match="cannot be sent"):
            await notifier.send(
                DeviceToken(DevicePlatform.ANDROID, "t"),
                EscalationNotification(
                    call_id=CallId("c"), title="t", body="b", caller_label="someone"
                ),
            )


class TestStaticVoiceProvider(VoiceProviderContract):
    @pytest.fixture
    def voices(self) -> StaticVoiceProvider:
        return StaticVoiceProvider()

    async def test_a_cloned_voice_wins_when_it_is_available(self) -> None:
        chosen = await resolve_voice(
            StaticVoiceProvider(),
            VoiceSelection(cloned_voice_id="cloned", persona_voice_id="bright"),
            locale="en",
        )
        assert chosen == "cloned"

    async def test_a_revoked_cloned_voice_falls_back_to_the_chosen_persona(self) -> None:
        # A voice that stops working changes how the call sounds and nothing else.
        chosen = await resolve_voice(
            StaticVoiceProvider(unavailable={"cloned"}),
            VoiceSelection(cloned_voice_id="cloned", persona_voice_id="bright"),
            locale="en",
        )
        assert chosen == "bright"

    async def test_when_everything_is_unavailable_the_default_still_answers(self) -> None:
        chosen = await resolve_voice(
            StaticVoiceProvider(unavailable={"cloned", "bright", "calm"}),
            VoiceSelection(cloned_voice_id="cloned", persona_voice_id="bright"),
            locale="en",
        )
        assert chosen == "calm"

    async def test_a_chosen_voice_that_cannot_speak_the_language_gives_way_to_one_that_can(
        self,
    ) -> None:
        # An English voice reading Hindi is a call nobody can follow.
        chosen = await resolve_voice(
            StaticVoiceProvider(), VoiceSelection(persona_voice_id="bright"), locale="hi-IN"
        )
        assert chosen == "gentle"

    async def test_a_chosen_voice_is_kept_in_any_language_it_speaks(self) -> None:
        chosen = await resolve_voice(
            StaticVoiceProvider(), VoiceSelection(persona_voice_id="bright"), locale="fr"
        )
        assert chosen == "bright"

    async def test_with_nothing_chosen_a_language_the_default_cannot_speak_gets_a_voice_that_can(
        self,
    ) -> None:
        assert await resolve_voice(StaticVoiceProvider(), VoiceSelection(), locale="hi") == "gentle"

    async def test_a_language_no_voice_speaks_still_gets_the_default(self) -> None:
        chosen = await resolve_voice(
            StaticVoiceProvider(), VoiceSelection(persona_voice_id="bright"), locale="de"
        )
        assert chosen == "calm"

    async def test_an_unavailable_voice_for_the_language_falls_back_to_the_default(self) -> None:
        chosen = await resolve_voice(
            StaticVoiceProvider(unavailable={"gentle"}), VoiceSelection(), locale="hi"
        )
        assert chosen == "calm"

    async def test_a_provider_without_cloning_declares_so(self) -> None:
        # No cloning declared means the interface shows no training flow at all.
        assert not StaticVoiceProvider().capabilities.cloning
        assert StaticVoiceProvider(cloning=True).capabilities.cloning
