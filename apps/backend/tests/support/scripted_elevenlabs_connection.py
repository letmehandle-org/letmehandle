"""An ElevenLabs agent in memory, speaking the protocol with synthetic silence for audio (D-013)."""

from __future__ import annotations

import base64
import itertools
from typing import TYPE_CHECKING, Any, Final

from tests.support.scripted_connection import ScriptedConnection, ScriptedService

if TYPE_CHECKING:
    from collections.abc import Mapping

# Twenty milliseconds of 16-bit audio at 16 kHz, the agent's default format.
CHUNK_BYTES: Final = 640
REPLY_WORDS: Final = "happy to help"


class ScriptedElevenLabsService(ScriptedService["ScriptedElevenLabsConnection"]):
    """Begins a conversation when opened with one and answers caller audio; never stops a reply."""

    def __init__(
        self,
        *,
        answer_audio: bool = True,
        chunks_per_reply: int = 3,
        input_format: str = "pcm_16000",
        output_format: str = "pcm_16000",
        begins: bool = True,
    ) -> None:
        super().__init__()
        self.answer_audio = answer_audio
        self.chunks_per_reply = chunks_per_reply
        self.input_format = input_format
        self.output_format = output_format
        self.begins = begins
        # When set, each conversation begins and then ends at once.
        self.hangs_up_after_beginning = False

    def _connection(self) -> ScriptedElevenLabsConnection:
        return ScriptedElevenLabsConnection(self)


class ScriptedElevenLabsConnection(ScriptedConnection[ScriptedElevenLabsService]):
    """One connection to the scripted agent: one conversation."""

    def __init__(self, service: ScriptedElevenLabsService) -> None:
        super().__init__(service)
        self._event_ids = itertools.count(1)

    def _answer(self, event: Mapping[str, Any]) -> None:
        if event.get("type") == "conversation_initiation_client_data" and self._service.begins:
            self.begin()
            if self._service.hangs_up_after_beginning:
                self.hang_up()
        elif "user_audio_chunk" in event and self._service.answer_audio:
            self.reply()

    def begin(self) -> None:
        self._push(
            {
                "type": "conversation_initiation_metadata",
                "conversation_initiation_metadata_event": {
                    "conversation_id": "conversation-scripted",
                    "user_input_audio_format": self._service.input_format,
                    "agent_output_audio_format": self._service.output_format,
                },
            }
        )

    def reply(self, *, chunks: int | None = None, words: str = REPLY_WORDS) -> list[int]:
        """Queue one spoken reply, and return the event ids of its audio."""
        self._push({"type": "agent_response", "agent_response_event": {"agent_response": words}})
        count = self._service.chunks_per_reply if chunks is None else chunks
        return [self.audio() for _ in range(count)]

    def audio(self, *, size: int = CHUNK_BYTES, event_id: int | None = None) -> int:
        """Queue one piece of agent audio, and return its event id."""
        number = next(self._event_ids) if event_id is None else event_id
        encoded = base64.b64encode(bytes(size)).decode("ascii")
        self._push({"type": "audio", "audio_event": {"audio_base_64": encoded, "event_id": number}})
        return number

    def interrupt(self) -> int:
        """The agent is talked over and stops, as the service decides for itself."""
        number = next(self._event_ids)
        self._push({"type": "interruption", "interruption_event": {"event_id": number}})
        return number

    def correct(self, original: str, said: str) -> None:
        self._push(
            {
                "type": "agent_response_correction",
                "agent_response_correction_event": {
                    "original_agent_response": original,
                    "corrected_agent_response": said,
                },
            }
        )

    def caller_said(self, text: str) -> None:
        self._push(
            {"type": "user_transcript", "user_transcription_event": {"user_transcript": text}}
        )

    def ping(self) -> int:
        number = next(self._event_ids)
        self._push({"type": "ping", "ping_event": {"event_id": number, "ping_ms": 20}})
        return number

    def sent_types(self) -> list[str]:
        return [str(event.get("type", "user_audio_chunk")) for event in self.sent]

    @property
    def opening(self) -> Mapping[str, Any]:
        """What the conversation was opened with."""
        return self.sent_of("conversation_initiation_client_data")[0]
