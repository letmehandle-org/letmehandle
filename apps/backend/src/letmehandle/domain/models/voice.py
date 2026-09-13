"""The voice a user has chosen, stored like every other preference (D-025)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VoiceSelection:
    """A cloned voice the user trained and a built-in one they picked (see `resolve_voice`)."""

    cloned_voice_id: str | None = None
    persona_voice_id: str | None = None
