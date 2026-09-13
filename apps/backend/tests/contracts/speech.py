"""What every speech provider must satisfy, mostly about releasing what a session holds."""

from __future__ import annotations

import asyncio
from abc import abstractmethod
from typing import TYPE_CHECKING

import pytest

from letmehandle.domain.models.audio import SPEECH_WIDEBAND, AudioFrame

if TYPE_CHECKING:
    from letmehandle.domain.ports.speech import SpeechProvider, SpeechSession

A_FRAME = AudioFrame(b"\x00\x01\x02\x03", SPEECH_WIDEBAND)


class SpeechProviderContract:
    """Every speech provider must pass this."""

    @pytest.fixture
    @abstractmethod
    def provider(self) -> SpeechProvider:
        """The implementation under test."""
        raise NotImplementedError

    def test_it_has_a_name_and_declares_capabilities(self, provider: SpeechProvider) -> None:
        assert provider.name.strip()
        assert provider.capabilities is not None

    def test_it_declares_at_least_one_language(self, provider: SpeechProvider) -> None:
        # A provider speaking nothing could never be selected for a user.
        assert provider.capabilities.languages
        assert provider.supported_locales()

    def test_language_matching_tolerates_a_regional_tag(self, provider: SpeechProvider) -> None:
        # A regional locale matches a provider listing its language.
        base = provider.capabilities.languages[0].split("-")[0]
        assert provider.capabilities.speaks(base)
        assert provider.capabilities.speaks(f"{base}-GB")

    def test_it_declares_an_input_format_it_accepts(self, provider: SpeechProvider) -> None:
        assert provider.capabilities.input_formats

    async def test_a_session_opens_and_closes(self, provider: SpeechProvider) -> None:
        session = await self._connect(provider)
        await session.close()

    async def test_closing_twice_is_safe(self, provider: SpeechProvider) -> None:
        # The caller and its context manager may both close.
        session = await self._connect(provider)
        await session.close()
        await session.close()

    async def test_leaving_the_context_closes_the_session(self, provider: SpeechProvider) -> None:
        # Leaving the context closes the session.
        async with await self._connect(provider) as session:
            await session.send_audio(A_FRAME)
        with pytest.raises(Exception):  # noqa: B017 - any refusal will do
            await session.send_audio(A_FRAME)

    async def test_audio_sent_produces_events(self, provider: SpeechProvider) -> None:
        async with await self._connect(provider) as session:
            await session.send_audio(A_FRAME)
            events = session.events()
            first = await asyncio.wait_for(anext(events), timeout=2)
            assert first is not None

    async def test_context_can_be_updated_mid_session_where_declared(
        self, provider: SpeechProvider
    ) -> None:
        if not provider.capabilities.context_updates_mid_session:
            pytest.skip("this provider cannot be told anything once a session is open")
        async with await self._connect(provider) as session:
            # The model must be told when the user joins the call.
            await session.update_context("the user has joined the call")

    async def test_interruption_discards_what_was_queued(self, provider: SpeechProvider) -> None:
        if not provider.capabilities.barge_in:
            pytest.skip("this provider cannot be interrupted")
        async with await self._connect(provider) as session:
            for _ in range(3):
                await session.send_audio(A_FRAME)
            await session.interrupt()
            # Whatever was already produced must not play out over the caller who interrupted.
            await self._assert_nothing_queued(session)

    async def test_cancelling_a_consumer_leaves_nothing_running(
        self, provider: SpeechProvider
    ) -> None:
        session = await self._connect(provider)

        async def consume() -> None:
            async for _event in session.events():
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await session.close()
        assert task.done()

    # ------------------------------------------------------------------ hooks

    async def _connect(self, provider: SpeechProvider) -> SpeechSession:
        return await provider.connect(
            system_context="a contract test",
            voice_id="any",
            greeting="Hello.",
            locale=provider.capabilities.languages[0],
            input_format=provider.capabilities.input_formats[0],
        )

    async def _assert_nothing_queued(self, session: object) -> None:
        """Assert nothing queued survives an interruption; override where there is no `queued`."""
        queued = getattr(session, "queued", None)
        assert queued == 0, "interruption must discard audio that was already produced"
