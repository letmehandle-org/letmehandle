"""A GPT-Live service in memory, its timeline moving with the synthetic audio it speaks (D-013)."""

from __future__ import annotations

import base64
from collections import deque
from typing import TYPE_CHECKING, Any

from tests.support.scripted_connection import ScriptedConnection, ScriptedService

if TYPE_CHECKING:
    from collections.abc import Mapping

# Each piece of audio the service speaks lasts this long, whatever the session's format.
DELTA_MS = 20
REPLY_TRANSCRIPT = "happy to help"
USAGE_SECONDS = 12.0

# A loud square wave and silence, twenty milliseconds of each, in every format the protocol carries.
_AUDIO = {
    "audio/pcmu": (bytes([0x80, 0x00]) * 80, bytes([0xFF]) * 160),
    "audio/pcma": (bytes([0xAA, 0x2A]) * 80, bytes([0xD5]) * 160),
    "audio/pcm": (b"\x20\x4e\xe0\xb1", b"\x00\x00"),
}


class ScriptedGptLiveService(ScriptedService["ScriptedGptLiveConnection"]):
    """Starts sessions, acknowledges appends, answers audio with speech and finalises on close."""

    def __init__(self, *, answer_audio: bool = True, speech_deltas: int = 3) -> None:
        super().__init__()
        self.answer_audio = answer_audio
        self.speech_deltas = speech_deltas
        # Errors that refuse the next sessions started, in order.
        self.start_errors: deque[Mapping[str, Any]] = deque()
        # When false, a session is never started.
        self.starts = True
        # When false, a close is never finalised.
        self.finalises = True

    def _connection(self) -> ScriptedGptLiveConnection:
        return ScriptedGptLiveConnection(self)


class ScriptedGptLiveConnection(ScriptedConnection[ScriptedGptLiveService]):
    """One connection to the scripted service."""

    def __init__(self, service: ScriptedGptLiveService) -> None:
        super().__init__(service)
        # Where this session is on its timeline, in milliseconds.
        self.now_ms = 0
        self._loud, self._silent = _AUDIO["audio/pcmu"]

    def _answer(self, event: Mapping[str, Any]) -> None:
        event_type = str(event.get("type"))
        match event_type:
            case "session.start":
                wire = event["session"]["audio"]["format"]
                loud, silent = _AUDIO[wire["type"]]
                if wire["type"] == "audio/pcm":
                    # Samples of a 16-bit wave: a pair per two samples, a zero per sample.
                    samples = wire["rate"] * DELTA_MS // 1000
                    loud, silent = loud * (samples // 2), silent * samples
                self._loud, self._silent = loud, silent
                if self._service.start_errors:
                    self._push({"type": "error", "error": self._service.start_errors.popleft()})
                elif self._service.starts:
                    self._push({"type": "session.started", "session": {"id": "sess_example"}})
            case (
                "session.instructions.append"
                | "session.thinking.append"
                | "session.commentary.append"
            ):
                self._push({"type": event_type.replace("append", "appended")})
            case "session.input_audio.append" if self._service.answer_audio:
                self.reply()
            case "session.close" if self._service.finalises:
                self.close_session("close_requested")

    def reply(self, *, speech: int | None = None, silence: int = 0, transcript: str = "") -> None:
        """Queue `speech` pieces of speech, its words, and then `silence` milliseconds of quiet."""
        deltas = self._service.speech_deltas if speech is None else speech
        start = self.now_ms
        for _ in range(deltas):
            self.audio(loud=True)
        words = transcript or REPLY_TRANSCRIPT
        self._push(_fragment("session.output_transcript.delta", words, start, self.now_ms))
        self.silence(silence)

    def audio(self, *, loud: bool) -> None:
        """Queue twenty milliseconds of speech or silence."""
        delta = base64.b64encode(self._loud if loud else self._silent).decode("ascii")
        self._push({"type": "session.output_audio.delta", "delta": delta})
        self.now_ms += DELTA_MS

    def caller_said(self, text: str, *, after_ms: int = 0, lasting_ms: int = 400) -> None:
        """Queue the caller's words, `after_ms` from now, with the silence the service streams."""
        self._words("session.input_transcript.delta", text, after_ms, lasting_ms)

    def assistant_said(self, text: str, *, after_ms: int = 0, lasting_ms: int = 400) -> None:
        """Queue the assistant's words, with silence where its audio would be."""
        self._words("session.output_transcript.delta", text, after_ms, lasting_ms)

    def silence(self, milliseconds: int) -> None:
        """Queue silence: what the service streams while nobody speaks."""
        for _ in range(milliseconds // DELTA_MS):
            self.audio(loud=False)

    def delegate(self, delegation_id: str) -> None:
        self._push(
            {
                "type": "session.delegation.created",
                "offset_ms": self.now_ms,
                "delegation": {"id": delegation_id, "type": "delegation", "target": "client"},
            }
        )

    def close_session(self, reason: str, *, seconds: float | None = USAGE_SECONDS) -> None:
        """Finalise the session and end the connection, as the service does."""
        usage = {} if seconds is None else {"usage": {"seconds": seconds}}
        self._push({"type": "session.closed", "reason": reason, **usage})
        self.hang_up()

    def appended_instructions(self) -> list[str]:
        return [str(event["content"]) for event in self.sent_of("session.instructions.append")]

    def _words(self, event_type: str, text: str, after_ms: int, lasting_ms: int) -> None:
        self.silence(after_ms)
        start = self.now_ms
        self.silence(lasting_ms)
        self._push(_fragment(event_type, text, start, self.now_ms))


def _fragment(event_type: str, text: str, start_ms: int, end_ms: int) -> dict[str, Any]:
    return {"type": event_type, "delta": text, "start_ms": start_ms, "end_ms": end_ms}
