"""What every speech provider must satisfy.

The contract is mostly about resources. A speech session holds a connection, a queue and at
least one task, and the ways it can be left holding them — an error mid-stream, a cancellation
mid-utterance, a caller that simply forgets — are the failures that take a service down
slowly rather than loudly.
"""

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
        # A provider that speaks nothing cannot be selected for any user, and the failure
        # would surface as a call that connects and says nothing.
        assert provider.capabilities.languages
        assert provider.supported_locales()

    def test_language_matching_tolerates_a_regional_tag(self, provider: SpeechProvider) -> None:
        # A user configured for en-GB must match a provider listing en, or the catalogue has
        # to enumerate every regional variant and will be missing one.
        base = provider.capabilities.languages[0].split("-")[0]
        assert provider.capabilities.speaks(base)
        assert provider.capabilities.speaks(f"{base}-GB")

    def test_it_declares_an_input_format_it_accepts(self, provider: SpeechProvider) -> None:
        assert provider.capabilities.input_formats

    async def test_a_session_opens_and_closes(self, provider: SpeechProvider) -> None:
        session = await self._connect(provider)
        await session.close()

    async def test_closing_twice_is_safe(self, provider: SpeechProvider) -> None:
        # Teardown paths overlap: the caller closes, and so does the context manager it is
        # inside. A second close must not raise.
        session = await self._connect(provider)
        await session.close()
        await session.close()

    async def test_leaving_the_context_closes_the_session(self, provider: SpeechProvider) -> None:
        # The reason a session is a context manager: the only way to leak one is to write code
        # that would not survive review.
        async with await self._connect(provider) as session:
            await session.send_audio(A_FRAME)
        with pytest.raises(Exception):  # noqa: B017 - any refusal will do; the point is that
            # a session outside its context does not quietly accept audio that goes nowhere.
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
            # Escalation depends on this: when the user joins, the model must be told that the
            # person it was speaking for is now on the call.
            await session.update_context("the user has joined the call")

    async def test_interruption_discards_what_was_queued(self, provider: SpeechProvider) -> None:
        if not provider.capabilities.barge_in:
            pytest.skip("this provider cannot be interrupted")
        async with await self._connect(provider) as session:
            for _ in range(3):
                await session.send_audio(A_FRAME)
            await session.interrupt()
            # The part implementations forget. Stopping production is not enough: whatever was
            # already produced will otherwise play out over the caller who interrupted.
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
        """Override where a provider exposes its queue differently.

        The default reads a `queued` property, which the in-memory session provides. A real
        adapter that cannot expose one asserts the same property another way rather than
        skipping it: this is the behaviour that decides whether interruption works.
        """
        queued = getattr(session, "queued", None)
        assert queued == 0, "interruption must discard audio that was already produced"
