"""The instructions returned to the provider when it asks what to do with a call.

Built with an XML writer rather than by formatting strings, because a conference name, a URL and
a stream parameter are all values that arrive from somewhere, and one unescaped ampersand turns
a call into an application error read aloud to the caller.
"""

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
    """Answer the caller straight into their call's own conference.

    Every choice here is one D-027 depends on. The caller's leaving ends the conference, so
    nobody else is left talking to an empty call. No beep, because the caller did not ask to
    join anything. The wait is silent rather than hold music, because to the caller this is
    simply a call that has been answered. The smallest jitter buffer, because the mixer's delay
    is conversational latency. Never recorded (D-013).

    The dial's action is asked for when the caller's time in the conference is over, however it
    ended. It is a request of its own on the caller's own leg, so the call is heard to end even
    when every conference callback saying so is lost.
    """
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
    """Connect the assistant's leg to this service's media websocket.

    The stream URL cannot carry a query string, so what identifies the call travels as stream
    parameters and comes back in the stream's first message.
    """
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
