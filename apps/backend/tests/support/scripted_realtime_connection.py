"""A realtime speech service in memory, speaking synthetic silence as its audio (D-013)."""

from __future__ import annotations

import base64
import itertools
from collections import deque
from typing import TYPE_CHECKING, Any

from tests.support.scripted_connection import Queued, ScriptedConnection, ScriptedService

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# Ten milliseconds of 16-bit audio at 24 kHz.
DELTA_BYTES = 480
REPLY_TRANSCRIPT = "happy to help"


class ScriptedRealtimeService(ScriptedService["ScriptedRealtimeConnection"]):
    """Acknowledges configuration, answers audio with a spoken reply, and honours a cancel."""

    def __init__(self, *, answer_audio: bool = True, deltas_per_reply: int = 3) -> None:
        super().__init__()
        self.answer_audio = answer_audio
        self.deltas_per_reply = deltas_per_reply
        # How many more connections to acknowledge and then end at once, and what they say first.
        self.hang_ups_after_configuring = 0
        self.last_words: Sequence[Mapping[str, Any]] = ()
        self._ids = itertools.count(1)

    def _connection(self) -> ScriptedRealtimeConnection:
        return ScriptedRealtimeConnection(self)

    def next_id(self) -> int:
        return next(self._ids)


class ScriptedRealtimeConnection(ScriptedConnection[ScriptedRealtimeService]):
    """One connection to the scripted realtime service."""

    def __init__(self, service: ScriptedRealtimeService) -> None:
        super().__init__(service)
        self._started: set[str] = set()

    def _answer(self, event: Mapping[str, Any]) -> None:
        match event.get("type"):
            case "session.update":
                self._push({"type": "session.updated", "session": event["session"]})
                if self._service.hang_ups_after_configuring:
                    self._service.hang_ups_after_configuring -= 1
                    for last_word in self._service.last_words:
                        self._push(last_word)
                    self.hang_up()
            case "input_audio_buffer.append" if self._service.answer_audio:
                self.reply()
            case "response.cancel":
                self._cancel()

    def _taken(self, queued: Queued) -> None:
        if queued.payload is not None and queued.response_id is not None:
            self._started.add(queued.response_id)

    def reply(self, *, deltas: int | None = None, transcript: str = REPLY_TRANSCRIPT) -> str:
        """Queue one whole spoken response, and return its id."""
        number = self._service.next_id()
        response_id, item_id = f"resp_{number}", f"item_{number}"
        self._push({"type": "response.created", "response": {"id": response_id}}, response_id)
        for _ in range(self._service.deltas_per_reply if deltas is None else deltas):
            self._push(audio_delta(response_id, item_id), response_id)
        self._push(
            {"type": "response.output_audio_transcript.delta", "delta": transcript}, response_id
        )
        self._push(
            {"type": "response.output_audio_transcript.done", "transcript": transcript},
            response_id,
        )
        self._push(
            {"type": "response.done", "response": {"id": response_id, "status": "completed"}},
            response_id,
        )
        return response_id

    def fail_response(self) -> str:
        """Queue a response the service could not produce, as running out of quota looks."""
        response_id = f"resp_{self._service.next_id()}"
        self._push({"type": "response.created", "response": {"id": response_id}})
        error = {"type": "insufficient_quota", "code": "insufficient_quota"}
        failed = {"id": response_id, "status": "failed", "status_details": {"error": error}}
        self._push({"type": "response.done", "response": failed})
        return response_id

    def caller_starts_speaking(self) -> None:
        self._push({"type": "input_audio_buffer.speech_started", "audio_start_ms": 0})

    def caller_stops_speaking(self) -> None:
        self._push({"type": "input_audio_buffer.speech_stopped", "audio_end_ms": 0})

    def caller_said(self, text: str) -> None:
        self._push({"type": "conversation.item.input_audio_transcription.delta", "delta": text})
        self._push(
            {"type": "conversation.item.input_audio_transcription.completed", "transcript": text}
        )

    def instructions_sent(self) -> list[str]:
        """The instructions of every configuration this connection was sent, in order."""
        return [str(event["session"]["instructions"]) for event in self.sent_of("session.update")]

    def _cancel(self) -> None:
        in_flight = {each.response_id for each in self._outbox if each.response_id is not None}
        if not in_flight:
            self._push({"type": "error", "error": {"code": "response_cancel_not_active"}})
            return
        self._outbox = deque(each for each in self._outbox if each.response_id not in in_flight)
        for response_id in sorted(in_flight & self._started):
            done = {"id": response_id, "status": "cancelled"}
            self._push({"type": "response.done", "response": done})


def audio_delta(response_id: str, item_id: str, *, size: int = DELTA_BYTES) -> dict[str, Any]:
    """One piece of synthetic model audio, as the current protocol spells it."""
    return {
        "type": "response.output_audio.delta",
        "response_id": response_id,
        "item_id": item_id,
        "output_index": 0,
        "content_index": 0,
        "delta": base64.b64encode(bytes(size)).decode("ascii"),
    }
