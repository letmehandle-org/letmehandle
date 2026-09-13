"""The XML instructions returned to the provider, written so that every value is escaped."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final
from xml.etree.ElementTree import Element, SubElement, tostring

from letmehandle.adapters.transport.twilio.callbacks import CONFERENCE_EVENTS

if TYPE_CHECKING:
    from collections.abc import Mapping

CONTENT_TYPE: Final = "application/xml"


def caller_conference(
    *,
    conference_name: str,
    status_callback_url: str,
    participant_label: str,
    dial_action_url: str,
) -> str:
    """Answer the caller into a silent, unrecorded conference that ends when they leave (D-027)."""
    response = Element("Response")
    dial = SubElement(response, "Dial", {"action": dial_action_url, "method": "POST"})
    conference = SubElement(
        dial,
        "Conference",
        {
            "beep": "false",
            "startConferenceOnEnter": "true",
            "endConferenceOnExit": "true",
            "waitUrl": "",
            "jitterBufferSize": "small",
            "participantLabel": participant_label,
            "record": "do-not-record",
            "statusCallback": status_callback_url,
            "statusCallbackMethod": "POST",
            "statusCallbackEvent": " ".join(CONFERENCE_EVENTS),
        },
    )
    conference.text = conference_name
    return _document(response)


def assistant_stream(*, stream_url: str, parameters: Mapping[str, str]) -> str:
    """Connect the assistant's leg to the media websocket, identifying it by stream parameters."""
    response = Element("Response")
    connect = SubElement(response, "Connect")
    stream = SubElement(connect, "Stream", {"url": stream_url})
    for name, value in parameters.items():
        SubElement(stream, "Parameter", {"name": name, "value": value})
    return _document(response)


def hang_up() -> str:
    """End a call this service has no business with: finished, unknown, or not ours."""
    response = Element("Response")
    SubElement(response, "Hangup")
    return _document(response)


def _document(root: Element) -> str:
    return tostring(root, encoding="unicode", xml_declaration=True)
