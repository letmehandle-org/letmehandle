"""Contracts for the smaller ports, one class per port."""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING

import pytest

from letmehandle.domain.errors import (
    CapabilityNotSupportedError,
    DomainError,
    InvariantError,
)
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.voice import VoiceSelection
from letmehandle.domain.ports.notification import (
    DeviceToken,
    EscalationNotification,
)
from letmehandle.domain.ports.voice import resolve_voice

if TYPE_CHECKING:
    from letmehandle.domain.models.identifiers import CallId
    from letmehandle.domain.ports.clock import Clock, IdGenerator
    from letmehandle.domain.ports.notification import NotificationProvider
    from letmehandle.domain.ports.otp import OTPProvider
    from letmehandle.domain.ports.voice import Voice, VoiceProvider

A_NUMBER = PhoneNumber.parse("+12025550143")


class ClockContract:
    @pytest.fixture
    @abstractmethod
    def clock(self) -> Clock:
        raise NotImplementedError

    def test_the_time_it_reports_knows_its_own_zone(self, clock: Clock) -> None:
        # A naive instant means whatever zone the machine is set to.
        assert clock.now().tzinfo is not None

    def test_it_reports_a_consistent_time(self, clock: Clock) -> None:
        assert clock.now() <= clock.now()


class IdGeneratorContract:
    @pytest.fixture
    @abstractmethod
    def generator(self) -> IdGenerator:
        raise NotImplementedError

    def test_it_produces_something(self, generator: IdGenerator) -> None:
        assert generator.generate().strip()

    def test_it_does_not_repeat_itself(self, generator: IdGenerator) -> None:
        produced = {generator.generate() for _ in range(100)}
        assert len(produced) == 100


class OTPProviderContract:
    @pytest.fixture
    @abstractmethod
    def otp(self) -> OTPProvider:
        raise NotImplementedError

    def test_it_says_whether_it_is_safe_for_production(self, otp: OTPProvider) -> None:
        # A mock provider in production lets anybody sign in as anybody.
        assert isinstance(otp.is_safe_for_production, bool)
        assert otp.name.strip()

    def test_only_a_provider_unsafe_for_production_fixes_the_code(self, otp: OTPProvider) -> None:
        # A real provider with a fixed code would give every account the same one.
        if otp.is_safe_for_production:
            assert otp.fixed_code is None

    async def test_it_accepts_a_code_for_a_number(self, otp: OTPProvider) -> None:
        # A provider that makes its own codes is asked to send one, and never handed one.
        if otp.issues_its_own_codes(A_NUMBER):
            await otp.send_own_code(A_NUMBER)
        else:
            await otp.send(A_NUMBER, "000000")

    async def test_only_a_provider_that_makes_its_codes_checks_them(self, otp: OTPProvider) -> None:
        if not otp.issues_its_own_codes(A_NUMBER):
            with pytest.raises(CapabilityNotSupportedError):
                await otp.send_own_code(A_NUMBER)
            with pytest.raises(CapabilityNotSupportedError):
                await otp.check(A_NUMBER, "000000")
            return
        # A code never sent signs nobody in, and a provider that makes its codes has none fixed.
        assert otp.fixed_code is None
        assert await otp.check(A_NUMBER, "000000") is False


class NotificationProviderContract:
    @pytest.fixture
    @abstractmethod
    def notifier(self) -> NotificationProvider:
        raise NotImplementedError

    @pytest.fixture
    @abstractmethod
    def call_id(self) -> CallId:
        raise NotImplementedError

    def _notification(self, call_id: CallId) -> EscalationNotification:
        return EscalationNotification(
            call_id=call_id,
            title="Your assistant needs you",
            body="A courier is at the gate and needs to know where to leave a parcel.",
            caller_label="a courier",
        )

    async def test_it_reports_an_outcome_rather_than_raising(
        self, notifier: NotificationProvider, call_id: CallId
    ) -> None:
        # A failed push is reported and never cancels an escalation.
        token = DeviceToken(notifier.platform, "a-token")
        outcome = await notifier.send(token, self._notification(call_id))
        assert outcome.status is not None

    async def test_an_invalid_token_is_distinguishable(
        self, notifier: NotificationProvider, call_id: CallId
    ) -> None:
        # A dead token is the outcome that requires removing the token rather than retrying.
        token = DeviceToken(notifier.platform, "a-token")
        outcome = await notifier.send(token, self._notification(call_id))
        assert outcome.token_should_be_removed == (
            not outcome.succeeded and outcome.status.value == "token_invalid"
        )


class VoiceProviderContract:
    @pytest.fixture
    @abstractmethod
    def voices(self) -> VoiceProvider:
        raise NotImplementedError

    def test_it_always_has_a_default(self, voices: VoiceProvider) -> None:
        # A provider with no default leaves a call with no voice.
        assert voices.default_voice_id.strip()

    async def test_it_lists_voices(self, voices: VoiceProvider) -> None:
        assert await voices.list_voices()

    async def test_listing_can_be_narrowed_by_language(self, voices: VoiceProvider) -> None:
        english = await voices.list_voices("en")
        assert all(voice.speaks("en") for voice in english)

    async def test_the_default_is_used_when_nothing_is_selected(
        self, voices: VoiceProvider
    ) -> None:
        chosen = await resolve_voice(voices, VoiceSelection(), locale="en")
        assert chosen == voices.default_voice_id

    async def test_a_selected_voice_is_used_when_it_is_available(
        self, voices: VoiceProvider
    ) -> None:
        # A voice other than the default, so a provider ignoring the selection fails.
        chosen_voice = await self._not_the_default(voices)
        chosen = await resolve_voice(
            voices, VoiceSelection(persona_voice_id=chosen_voice.id), locale="en"
        )
        assert chosen == chosen_voice.id

    async def test_an_unavailable_selection_falls_through_rather_than_failing(
        self, voices: VoiceProvider
    ) -> None:
        # A revoked or broken voice changes how the call sounds and never silences it.
        chosen = await resolve_voice(
            voices, VoiceSelection(persona_voice_id="no-such-voice"), locale="en"
        )
        assert chosen == voices.default_voice_id

    async def test_a_missing_locale_is_refused(self, voices: VoiceProvider) -> None:
        with pytest.raises(InvariantError):
            await resolve_voice(voices, VoiceSelection(), locale="  ")

    async def test_preview_matches_what_the_provider_declares(self, voices: VoiceProvider) -> None:
        # The declared preview capability matches whether samples are served.
        if voices.capabilities.preview:
            sample = await voices.preview(voices.default_voice_id)
            assert sample.audio
            assert sample.media_type.strip()
        else:
            with pytest.raises(CapabilityNotSupportedError):
                await voices.preview(voices.default_voice_id)

    @staticmethod
    async def _not_the_default(voices: VoiceProvider) -> Voice:
        """An English catalogue voice that is not the fallback, so a test can tell them apart."""
        catalogue = await voices.list_voices("en")
        for voice in catalogue:
            if voice.id != voices.default_voice_id:
                return voice
        pytest.skip("this provider offers only its default voice")

    async def test_an_unknown_voice_is_never_a_key_error(self, voices: VoiceProvider) -> None:
        # An unknown voice reaches the caller as a domain error, never a KeyError.
        with pytest.raises(DomainError):
            await voices.preview("no-such-voice")
