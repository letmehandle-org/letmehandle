"""What the user has chosen about how the assistant sounds.

A model rather than part of the voice port, because this is something the user stored: it is
read back, edited and persisted like every other preference. The port describes what a provider
*offers* — a catalogue, and what it is capable of — and those are different things that happen
to share a subject.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VoiceSelection:
    """What a user has chosen.

    Both fields optional, and both meaning something different from the other: a cloned voice
    the user trained, and a built-in one they picked. The resolution order between them is the
    domain's, not a provider's — see `resolve_voice`.
    """

    cloned_voice_id: str | None = None
    persona_voice_id: str | None = None
