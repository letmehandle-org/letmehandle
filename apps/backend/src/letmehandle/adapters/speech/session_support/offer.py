"""What a provider offers, checked once when it is built and again for each session asked of it.

Refused at construction rather than at the first connect wherever it can be, so a
misconfiguration stops the application starting instead of failing the first call that uses it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from letmehandle.adapters.audio.conversion import can_convert
from letmehandle.domain.errors import CapabilityNotSupportedError, InvariantError
from letmehandle.domain.ports.speech import SpeechCapabilities

if TYPE_CHECKING:
    from collections.abc import Sequence

    from letmehandle.domain.models.audio import AudioFormat


def checked_capabilities(
    *,
    wire_format: AudioFormat,
    languages: Sequence[str],
    input_formats: Sequence[AudioFormat],
    output_format: AudioFormat,
    barge_in: bool,
    context_updates_mid_session: bool,
    reconnection: bool,
) -> SpeechCapabilities:
    """Capabilities whose languages and formats this adapter can actually honour."""
    if not languages:
        raise InvariantError("a speech provider that speaks no language can serve nobody")
    if not input_formats:
        raise InvariantError("a speech provider must accept audio in at least one format")
    refused = [str(each) for each in input_formats if not can_convert(each, wire_format)]
    if refused:
        raise InvariantError(f"audio in these formats cannot be converted: {refused}")
    if not can_convert(wire_format, output_format):
        raise InvariantError(f"audio cannot be produced in {output_format}")
    return SpeechCapabilities(
        barge_in=barge_in,
        context_updates_mid_session=context_updates_mid_session,
        reconnection=reconnection,
        languages=tuple(languages),
        input_formats=tuple(input_formats),
        output_format=output_format,
    )


def check_session_request(
    provider: str,
    capabilities: SpeechCapabilities,
    *,
    locale: str,
    input_format: AudioFormat,
    voice_id: str,
) -> None:
    """Refuse a session this provider has not declared it can hold."""
    if not capabilities.speaks(locale):
        raise CapabilityNotSupportedError(provider, f"speaking {locale}")
    if input_format not in capabilities.input_formats:
        raise CapabilityNotSupportedError(provider, f"audio input in {input_format}")
    if not voice_id.strip():
        raise InvariantError("a speech session needs a voice to speak in")
