"""A telephony provider in this process: its REST API, its callbacks and its media streams.

What it reproduces is what the documentation describes, and what the documentation warns about.
Callbacks are separate HTTP requests signed exactly as the provider signs them — the public URL
the provider was configured with, every form parameter, HMAC-SHA1 under the auth token — and sent
to the application running on loopback, which is the situation behind a tunnel: the Host a
request arrives with is not the URL that was signed. The assistant's leg fetches its
instructions from the application and opens a real websocket to it, with a signed handshake.

A test can hold callbacks back and then deliver them reordered, twice, or not at all; have every
callback sent twice as it happens; move the provider to a restarted application; decide how
a dialled person answers, or does not; drop the assistant's websocket; stop the stream without
closing it; and hang up either party first. No account, no number, and no network beyond
loopback.

Every identifier it hands out says it is simulated, and every number is in a range reserved for
fiction.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import itertools
import json
import socket
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Final
from urllib.parse import parse_qsl, urlsplit
from xml.etree.ElementTree import fromstring

import httpx
import uvicorn
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from letmehandle.adapters.transport.twilio.signature import SIGNATURE_HEADER, compute_signature
from letmehandle.bootstrap import build_call_transport, build_reported_calls
from letmehandle.config.settings import TelephonyProviderName
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.main import create_app
from tests.support.config import make_settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Coroutine

    from fastapi import FastAPI

    from letmehandle.adapters.transport.twilio.transport import TwilioCallTransport
    from letmehandle.bootstrap import CallTransportBinding
    from letmehandle.config.settings import Settings
    from letmehandle.domain.ports.call_transport import CallEvent

SIMULATED_ACCOUNT: Final = "account-simulated"
SIMULATED_TOKEN: Final = "auth-token-simulated"
SIMULATED_APP: Final = "application-simulated"
PUBLIC_BASE_URL: Final = "https://calls.example.com"
OUR_NUMBER: Final = PhoneNumber.parse("+12025550100")
CALLER_NUMBER: Final = "+12025550123"
IDEMPOTENCY_HEADER: Final = "I-Twilio-Idempotency-Token"

_API_PREFIX: Final = f"/2010-04-01/Accounts/{SIMULATED_ACCOUNT}"


class Answering(StrEnum):
    """How a dialled phone behaves."""

    ANSWERS = "answers"
    RINGS_OUT = "rings_out"
    BUSY = "busy"
    FAILS = "fails"
    VOICEMAIL = "voicemail"
    KEEPS_RINGING = "keeps_ringing"


@dataclass
class Delivery:
    """One callback the provider sends, kept so a test can send it again or not at all."""

    path_and_query: str
    params: list[tuple[str, str]]
    token: str
    status: int | None = None


@dataclass(eq=False)
class SimulatedLeg:
    call_sid: str
    label: str
    to: str
    status_callback: str | None
    conference: SimulatedConference | None
    # Where the caller's dial reports its end: the provider asks it what next once they leave.
    dial_action: str | None = None
    in_conference: bool = False
    finished: bool = False
    answered: bool = False
    progress: itertools.count[int] = field(default_factory=itertools.count)
    socket: ClientConnection | None = None
    stream_sid: str | None = None
    sent_to_call: list[bytes] = field(default_factory=list)
    clears: int = 0
    muted: bool = False
    coaching: str | None = None


@dataclass(eq=False)
class SimulatedConference:
    name: str
    sid: str
    status_callback: str
    legs: list[SimulatedLeg] = field(default_factory=list)
    sequence: itertools.count[int] = field(default_factory=lambda: itertools.count(1))
    started: bool = False
    ended: bool = False


class SimulatedTwilio:
    """The provider, answering the application's requests and calling it back."""

    def __init__(self, *, public_base_url: str = PUBLIC_BASE_URL) -> None:
        self.public_base_url = public_base_url
        self.rest = httpx.MockTransport(self._handle_rest)
        self.answering: dict[str, Answering] = {}
        self.delivered: list[Delivery] = []
        self.held: list[Delivery] | None = None
        self.requests: list[tuple[str, str, list[tuple[str, str]]]] = []
        self.handshake_statuses: list[int] = []
        self.conferences: dict[str, SimulatedConference] = {}
        self.legs: dict[str, SimulatedLeg] = {}
        self._app_url = ""
        self._client: httpx.AsyncClient | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        # Readers live as long as their socket, so waiting for things to settle ignores them.
        self._readers: set[asyncio.Task[None]] = set()
        self._ids = itertools.count(1)
        self.fail_next_rest: int | None = None
        # Every callback sent a second time, with the same idempotency token, straight after the
        # first: what the provider does when it did not see the first acknowledged.
        self.duplicate_callbacks = False
        self.sign_handshake_with_slash = False
        self.sign_handshake_url: str | None = None

    # ------------------------------------------------------------- lifecycle

    def attach(self, app_url: str) -> None:
        self._app_url = app_url
        self._client = httpx.AsyncClient(base_url=app_url, timeout=5.0)

    async def move_to(self, app_url: str) -> None:
        """Call back an application started in place of the one this was attached to."""
        if self._client is not None:
            await self._client.aclose()
        self.attach(app_url)

    async def close(self) -> None:
        for leg in self.legs.values():
            if leg.socket is not None:
                await leg.socket.close()
        everything = [*self._tasks, *self._readers]
        for task in everything:
            task.cancel()
        await asyncio.gather(*everything, return_exceptions=True)
        if self._client is not None:
            await self._client.aclose()

    async def settle(self, transport: TwilioCallTransport) -> None:
        """Wait until nothing is in flight on either side."""
        async with asyncio.timeout(10):
            while True:
                for _ in range(5):
                    await asyncio.sleep(0.01)
                if self._tasks:
                    await asyncio.gather(*self._tasks, return_exceptions=True)
                    continue
                if transport.pending_tasks:
                    await transport.settled()
                    continue
                return

    # ------------------------------------------------------ what a test does

    async def place_call(
        self,
        call_sid: str = "CAsim-caller",
        caller: str = CALLER_NUMBER,
        *,
        forwarded_from: PhoneNumber | None = None,
    ) -> str:
        """A call arrives at our number, forwarded from `forwarded_from`'s line when it is given.

        Returns the instructions the application gave.
        """
        params = [
            ("AccountSid", SIMULATED_ACCOUNT),
            ("CallSid", call_sid),
            ("From", caller),
            ("To", OUR_NUMBER.value),
            ("CallStatus", "ringing"),
            ("Direction", "inbound"),
        ]
        if forwarded_from is not None:
            params.append(("ForwardedFrom", forwarded_from.value))
        response = await self.post_signed("/telephony/voice/incoming", params)
        document = response.text
        root = fromstring(document)  # noqa: S314 - the application under test wrote it
        dial = root.find("./Dial")
        conference_element = root.find("./Dial/Conference")
        if dial is None or conference_element is None:
            return document
        name = conference_element.text or ""
        conference = self.conferences.get(name)
        if conference is None:
            conference = SimulatedConference(
                name=name,
                sid=f"CFsim-{next(self._ids)}",
                status_callback=conference_element.attrib["statusCallback"],
            )
            self.conferences[name] = conference
        leg = SimulatedLeg(
            call_sid=call_sid,
            label=conference_element.attrib["participantLabel"],
            to=OUR_NUMBER.value,
            status_callback=None,
            conference=conference,
            dial_action=dial.attrib.get("action"),
        )
        self.legs[call_sid] = leg
        self._join(conference, leg)
        return document

    async def send_caller_audio(self, call_sid: str, audio: bytes, *, frames: int = 1) -> None:
        """The call's audio reaching the assistant's leg, in the provider's own framing."""
        # The application counts a socket once its start arrives, which can be before this side
        # has finished recording the connection it opened, so wait for that too.
        await eventually(lambda: self._has_streaming_assistant(call_sid))
        leg = self.assistant_of(call_sid)
        assert leg.socket is not None
        for chunk in range(1, frames + 1):
            await leg.socket.send(
                json.dumps(
                    {
                        "event": "media",
                        "sequenceNumber": str(chunk + 2),
                        "streamSid": leg.stream_sid,
                        "media": {
                            "track": "inbound",
                            "chunk": str(chunk),
                            "timestamp": str(chunk * 20),
                            "payload": base64.b64encode(audio).decode(),
                        },
                    }
                )
            )

    async def press_digit(self, call_sid: str, digit: str) -> None:
        leg = self.assistant_of(call_sid)
        assert leg.socket is not None
        await leg.socket.send(
            json.dumps(
                {
                    "event": "dtmf",
                    "streamSid": leg.stream_sid,
                    "sequenceNumber": "9",
                    "dtmf": {"track": "inbound_track", "digit": digit},
                }
            )
        )

    async def caller_hangs_up(self, call_sid: str) -> None:
        await self._hang_up(self.legs[call_sid])

    async def user_hangs_up(self, number: PhoneNumber) -> None:
        leg = next(
            each for each in self.legs.values() if each.to == number.value and not each.finished
        )
        await self._hang_up(leg)

    async def drop_assistant_socket(self, call_sid: str) -> None:
        """The websocket closes with no stop message, and the leg it carried ends."""
        leg = self.assistant_of(call_sid)
        assert leg.socket is not None
        await leg.socket.close()

    async def stop_stream_without_closing(self, call_sid: str) -> None:
        """A stop message, and a socket left open for the application to close."""
        leg = self.assistant_of(call_sid)
        assert leg.socket is not None
        await leg.socket.send(
            json.dumps({"event": "stop", "sequenceNumber": "99", "streamSid": leg.stream_sid})
        )

    def hold(self) -> None:
        """Keep callbacks back until `release`."""
        self.held = []

    async def release(
        self, order: Callable[[list[Delivery]], list[Delivery]] | None = None
    ) -> None:
        held, self.held = self.held or [], None
        for delivery in order(held) if order is not None else held:
            await self.deliver(delivery)

    async def deliver(self, delivery: Delivery) -> httpx.Response:
        """Send a callback, again if it has been sent before, with the same token."""
        assert self._client is not None
        url = self.public_base_url + delivery.path_and_query
        response = await self._client.post(
            delivery.path_and_query,
            data=_fields(delivery.params),
            headers={
                SIGNATURE_HEADER: compute_signature(url, delivery.params, SIMULATED_TOKEN),
                IDEMPOTENCY_HEADER: delivery.token,
            },
        )
        delivery.status = response.status_code
        self.delivered.append(delivery)
        return response

    async def post_signed(
        self, path_and_query: str, params: list[tuple[str, str]], *, token: str = SIMULATED_TOKEN
    ) -> httpx.Response:
        """A request signed as the provider signs it, with whatever token it is given."""
        assert self._client is not None
        url = self.public_base_url + path_and_query
        return await self._client.post(
            path_and_query,
            data=_fields(params),
            headers={SIGNATURE_HEADER: compute_signature(url, params, token)},
        )

    def _has_streaming_assistant(self, call_sid: str) -> bool:
        return any(
            leg.label.startswith("assistant") and leg.socket is not None
            for leg in self.conference_of(call_sid).legs
        )

    def assistant_of(self, call_sid: str) -> SimulatedLeg:
        conference = self.conference_of(call_sid)
        return next(
            leg
            for leg in reversed(conference.legs)
            if leg.label.startswith("assistant") and leg.socket is not None
        )

    def conference_of(self, call_sid: str) -> SimulatedConference:
        conference = self.legs[call_sid].conference
        assert conference is not None
        return conference

    def user_leg(self, number: PhoneNumber) -> SimulatedLeg:
        return next(leg for leg in reversed(self.legs.values()) if leg.to == number.value)

    # --------------------------------------------------------------- REST API

    async def _handle_rest(self, request: httpx.Request) -> httpx.Response:
        params = parse_qsl(request.content.decode(), keep_blank_values=True)
        path = request.url.path
        self.requests.append((request.method, path, params))
        expected = (
            "Basic " + base64.b64encode(f"{SIMULATED_ACCOUNT}:{SIMULATED_TOKEN}".encode()).decode()
        )
        if request.headers.get("Authorization") != expected:
            return _error(401, 20003)
        if self.fail_next_rest is not None:
            status, self.fail_next_rest = self.fail_next_rest, None
            return _error(status, 20500)
        assert path.startswith(_API_PREFIX), path
        parts = path.removeprefix(_API_PREFIX).removesuffix(".json").strip("/").split("/")
        fields = dict(params)
        match (request.method, parts):
            case ("POST", ["Conferences", name, "Participants"]):
                return self._create_participant(name, params)
            case ("POST", ["Conferences", sid, "Participants", call_sid]):
                return self._update_participant(sid, call_sid, fields)
            case ("DELETE", ["Conferences", sid, "Participants", call_sid]):
                return await self._remove_participant(sid, call_sid)
            case ("POST", ["Conferences", sid]):
                return await self._end_conference(sid, "conference-ended-via-api")
            case ("POST", ["Calls", call_sid]):
                return await self._update_call(call_sid, fields["Status"])
        return _error(404, 20404)

    def _create_participant(self, name: str, params: list[tuple[str, str]]) -> httpx.Response:
        fields = dict(params)
        conference = self.conferences.get(name)
        if conference is None or conference.ended:
            conference = SimulatedConference(
                name=name,
                sid=f"CFsim-{next(self._ids)}",
                status_callback=fields["ConferenceStatusCallback"],
            )
            self.conferences[name] = conference
        if any(leg.label == fields["Label"] and not leg.finished for leg in conference.legs):
            return _error(400, 16025)
        call_sid = f"CAsim-{fields['Label']}-{next(self._ids)}"
        leg = SimulatedLeg(
            call_sid=call_sid,
            label=fields["Label"],
            to=fields["To"],
            status_callback=fields["StatusCallback"],
            conference=conference,
        )
        self.legs[call_sid] = leg
        if leg.to.startswith("app:"):
            self._later(self._assistant_leg(leg))
        else:
            self._later(self._user_leg(leg, detect=fields.get("MachineDetection") == "Enable"))
        return httpx.Response(201, json={"call_sid": call_sid, "label": leg.label})

    def _update_participant(
        self, sid: str, call_sid: str, fields: dict[str, str]
    ) -> httpx.Response:
        leg = self.legs.get(call_sid)
        if (
            leg is None
            or not leg.in_conference
            or leg.conference is None
            or leg.conference.sid != sid
        ):
            return _error(404, 20404)
        leg.muted = fields.get("Muted") == "true"
        leg.coaching = fields.get("CallSidToCoach") if fields.get("Coaching") == "true" else None
        return httpx.Response(200, json={"call_sid": call_sid})

    async def _remove_participant(self, sid: str, call_sid: str) -> httpx.Response:
        leg = self.legs.get(call_sid)
        if (
            leg is None
            or not leg.in_conference
            or leg.conference is None
            or leg.conference.sid != sid
        ):
            return _error(404, 20404)
        self._later(self._leave(leg, completed=True))
        return httpx.Response(204)

    async def _end_conference(self, sid: str, reason: str) -> httpx.Response:
        conference = next((each for each in self.conferences.values() if each.sid == sid), None)
        if conference is None or conference.ended:
            return _error(404, 20404)
        self._later(self._close_conference(conference, reason, None))
        return httpx.Response(200, json={"sid": sid, "status": "completed"})

    async def _update_call(self, call_sid: str, status: str) -> httpx.Response:
        leg = self.legs.get(call_sid)
        if leg is None or leg.finished:
            return _error(404, 20404)
        if status == "canceled" and not leg.answered:
            leg.finished = True
            self._later(self._progress(leg, "canceled"))
        else:
            self._later(self._hang_up(leg))
        return httpx.Response(200, json={"sid": call_sid})

    # ------------------------------------------------------------- behaviour

    async def _user_leg(self, leg: SimulatedLeg, *, detect: bool) -> None:
        answering = self.answering.get(leg.to, Answering.KEEPS_RINGING)
        await self._progress(leg, "initiated")
        if answering is Answering.FAILS:
            leg.finished = True
            await self._progress(leg, "failed")
            return
        await self._progress(leg, "ringing")
        match answering:
            case Answering.RINGS_OUT:
                leg.finished = True
                await self._progress(leg, "no-answer")
            case Answering.BUSY:
                leg.finished = True
                await self._progress(leg, "busy")
            case Answering.ANSWERS | Answering.VOICEMAIL:
                if leg.finished:
                    return
                leg.answered = True
                extra = []
                if detect:
                    human = answering is Answering.ANSWERS
                    extra = [("AnsweredBy", "human" if human else "machine_start")]
                await self._progress(leg, "in-progress", extra)
                assert leg.conference is not None
                # Hung up while its answer was being delivered: it never reaches the conference.
                if not leg.finished:
                    self._join(leg.conference, leg)
            case Answering.KEEPS_RINGING | Answering.FAILS:
                pass

    async def _assistant_leg(self, leg: SimulatedLeg) -> None:
        query = dict(parse_qsl(urlsplit(leg.to.removeprefix("app:")).query))
        await self._progress(leg, "initiated")
        leg.answered = True
        await self._progress(leg, "in-progress")
        response = await self.post_signed(
            "/telephony/voice/assistant",
            [
                ("AccountSid", SIMULATED_ACCOUNT),
                ("CallSid", leg.call_sid),
                ("From", OUR_NUMBER.value),
                ("To", leg.to),
                *query.items(),
            ],
        )
        stream = fromstring(response.text).find("./Connect/Stream")  # noqa: S314 - ours
        if stream is None:
            leg.finished = True
            await self._progress(leg, "completed")
            return
        public_url = stream.attrib["url"]
        parameters = {each.attrib["name"]: each.attrib["value"] for each in stream}
        signed_url = self.sign_handshake_url or (
            public_url + "/" if self.sign_handshake_with_slash else public_url
        )
        loopback = self._app_url.replace("http://", "ws://") + urlsplit(public_url).path
        try:
            leg.socket = await connect(
                loopback,
                additional_headers={
                    SIGNATURE_HEADER: compute_signature(signed_url, [], SIMULATED_TOKEN)
                },
            )
        except Exception as refused:  # noqa: BLE001 - the refusal is what a test inspects
            status = getattr(getattr(refused, "response", None), "status_code", 0)
            self.handshake_statuses.append(status)
            leg.finished = True
            await self._progress(leg, "completed")
            return
        leg.stream_sid = f"MZsim-{next(self._ids)}"
        await leg.socket.send(
            json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"})
        )
        await leg.socket.send(
            json.dumps(
                {
                    "event": "start",
                    "sequenceNumber": "1",
                    "streamSid": leg.stream_sid,
                    "start": {
                        "accountSid": SIMULATED_ACCOUNT,
                        "streamSid": leg.stream_sid,
                        "callSid": leg.call_sid,
                        "tracks": ["inbound"],
                        "mediaFormat": {
                            "encoding": "audio/x-mulaw",
                            "sampleRate": 8000,
                            "channels": 1,
                        },
                        "customParameters": parameters,
                    },
                }
            )
        )
        assert leg.conference is not None
        self._join(leg.conference, leg)
        reader = asyncio.get_running_loop().create_task(self._read_stream(leg))
        self._readers.add(reader)
        reader.add_done_callback(self._readers.discard)

    async def _read_stream(self, leg: SimulatedLeg) -> None:
        assert leg.socket is not None
        with contextlib.suppress(ConnectionClosed):
            async for text in leg.socket:
                message = json.loads(text)
                match message["event"]:
                    case "media":
                        leg.sent_to_call.append(base64.b64decode(message["media"]["payload"]))
                    case "clear":
                        leg.clears += 1
                    case _:
                        pass
        # The socket is gone, so the leg that carried it has nothing left to do and hangs up.
        if not leg.finished:
            self._later(self._hang_up(leg))

    async def _hang_up(self, leg: SimulatedLeg) -> None:
        if leg.finished and not leg.in_conference:
            return
        conference = leg.conference
        if conference is not None and leg.in_conference and leg.label == "caller":
            await self._close_conference(
                conference, "participant-with-end-conference-on-exit-left", leg
            )
            return
        await self._leave(leg, completed=True)

    async def _leave(self, leg: SimulatedLeg, *, completed: bool) -> None:
        if leg.finished and not leg.in_conference:
            return
        leg.finished = True
        if leg.socket is not None:
            with contextlib.suppress(ConnectionClosed):
                await leg.socket.send(
                    json.dumps(
                        {"event": "stop", "sequenceNumber": "98", "streamSid": leg.stream_sid}
                    )
                )
            await leg.socket.close()
        conference = leg.conference
        if conference is not None and leg.in_conference:
            leg.in_conference = False
            await self._conference_event(conference, "participant-leave", leg)
        if completed and leg.status_callback is not None:
            await self._progress(leg, "completed")
        if leg.dial_action is not None:
            await self._send(
                leg.dial_action,
                [
                    ("AccountSid", SIMULATED_ACCOUNT),
                    ("CallSid", leg.call_sid),
                    ("CallStatus", "completed"),
                    ("DialCallStatus", "completed"),
                ],
            )

    async def _close_conference(
        self, conference: SimulatedConference, reason: str, ending: SimulatedLeg | None
    ) -> None:
        if conference.ended:
            return
        conference.ended = True
        if ending is not None:
            await self._leave(ending, completed=True)
        for leg in list(conference.legs):
            if leg.in_conference:
                await self._leave(leg, completed=True)
        await self._conference_event(conference, "conference-end", None, reason=reason)

    def _join(self, conference: SimulatedConference, leg: SimulatedLeg) -> None:
        leg.in_conference = True
        conference.legs.append(leg)
        self._later(self._joined(conference, leg))

    async def _joined(self, conference: SimulatedConference, leg: SimulatedLeg) -> None:
        await self._conference_event(conference, "participant-join", leg)
        if not conference.started and sum(each.in_conference for each in conference.legs) >= 2:
            conference.started = True
            await self._conference_event(conference, "conference-start", None)

    async def _conference_event(
        self,
        conference: SimulatedConference,
        event: str,
        leg: SimulatedLeg | None,
        *,
        reason: str | None = None,
    ) -> None:
        params = [
            ("AccountSid", SIMULATED_ACCOUNT),
            ("ConferenceSid", conference.sid),
            ("FriendlyName", conference.name),
            ("SequenceNumber", str(next(conference.sequence))),
            ("StatusCallbackEvent", event),
            ("Timestamp", "Mon, 01 Jun 2026 12:00:00 +0000"),
        ]
        if leg is not None:
            params += [
                ("CallSid", leg.call_sid),
                ("ParticipantLabel", leg.label),
                ("Muted", "false"),
                ("Hold", "false"),
                ("Coaching", "false"),
            ]
        if reason is not None:
            params.append(("ReasonConferenceEnded", reason))
        await self._send(conference.status_callback, params)

    async def _progress(
        self, leg: SimulatedLeg, status: str, extra: list[tuple[str, str]] | None = None
    ) -> None:
        if leg.status_callback is None:
            return
        params = [
            ("AccountSid", SIMULATED_ACCOUNT),
            ("CallSid", leg.call_sid),
            ("CallStatus", status),
            ("SequenceNumber", str(next(leg.progress))),
            *(extra or []),
        ]
        await self._send(leg.status_callback, params)

    async def _send(self, url: str, params: list[tuple[str, str]]) -> None:
        assert url.startswith(self.public_base_url), url
        delivery = Delivery(
            path_and_query=url.removeprefix(self.public_base_url),
            params=params,
            token=f"idempotency-{next(self._ids)}",
        )
        if self.held is not None:
            self.held.append(delivery)
            return
        await self.deliver(delivery)
        if self.duplicate_callbacks:
            await self.deliver(delivery)

    def _later(self, work: Coroutine[object, object, None]) -> None:
        task = asyncio.get_running_loop().create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


async def eventually(condition: Callable[[], bool], *, seconds: float = 5.0) -> None:
    """Wait for something observed over a socket, which offers nothing to wait on but itself."""
    async with asyncio.timeout(seconds):
        while not condition():  # noqa: ASYNC110 - a plain attribute, not an event
            await asyncio.sleep(0.01)


def _fields(params: list[tuple[str, str]]) -> dict[str, list[str]]:
    fields: dict[str, list[str]] = {}
    for name, value in params:
        fields.setdefault(name, []).append(value)
    return fields


def _error(status: int, code: int) -> httpx.Response:
    return httpx.Response(status, json={"code": code, "status": status})


# ------------------------------------------------------------------ a deployment


@dataclass
class Deployment:
    """The application, its streaming transport, and the provider it is talking to."""

    app: FastAPI
    transport: TwilioCallTransport
    provider: SimulatedTwilio
    events: list[CallEvent]
    binding: CallTransportBinding

    async def settle(self) -> None:
        await self.provider.settle(self.transport)

    def kinds(self) -> list[tuple[str, str | None, str | None]]:
        return [
            (
                event.kind.value,
                event.participant.value if event.participant else None,
                event.outcome.value if event.outcome else None,
            )
            for event in self.events
        ]


def telephony_settings(public_base_url: str = PUBLIC_BASE_URL) -> Settings:
    """Settings for a deployment whose telephony account is the simulated one."""
    return make_settings(
        telephony_provider=TelephonyProviderName.TWILIO,
        telephony_account_id=SIMULATED_ACCOUNT,
        telephony_auth_token=SIMULATED_TOKEN,
        telephony_numbers=(OUR_NUMBER,),
        telephony_app_id=SIMULATED_APP,
        telephony_webhook_base_url=public_base_url,
    )


@asynccontextmanager
async def simulated_deployment(
    *, public_base_url: str = PUBLIC_BASE_URL, collect_events: bool = True
) -> AsyncIterator[Deployment]:
    """The whole application on loopback, wired to a simulated provider, and torn down after.

    Events are collected into the deployment unless the test reads them itself: the transport's
    event stream has one reader, as the orchestrator is its one reader in the product.
    """
    from letmehandle.adapters.transport.twilio.transport import TwilioCallTransport

    provider = SimulatedTwilio(public_base_url=public_base_url)
    settings = telephony_settings(public_base_url)
    binding = build_call_transport(
        settings, reported_calls=build_reported_calls(), http_transport=provider.rest
    )
    assert binding is not None
    transport = binding.transport
    assert isinstance(transport, TwilioCallTransport)
    # The transport is handed to the application rather than configured on it: this deployment has
    # no storage, and whoever needs calls orchestrated builds the orchestrator on the transport.
    app = create_app(make_settings(), telephony=binding)
    events: list[CallEvent] = []

    async def collect() -> None:
        if not collect_events:
            return
        async for event in transport.events():
            events.append(event)

    async with serving(app) as app_url:
        provider.attach(app_url)
        collector = asyncio.get_running_loop().create_task(collect())
        try:
            yield Deployment(
                app=app, transport=transport, provider=provider, events=events, binding=binding
            )
        finally:
            await provider.close()
            collector.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait_for(collector, timeout=5)


@asynccontextmanager
async def serving(app: FastAPI) -> AsyncIterator[str]:
    """The application on loopback over real HTTP, lifespan and all, until the block exits."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    config = uvicorn.Config(
        app, log_config=None, access_log=False, lifespan="on", ws="websockets-sansio"
    )
    server = uvicorn.Server(config)
    task = asyncio.get_running_loop().create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(10):
            # The server exposes a flag, not an event, so it is polled.
            while not server.started:  # noqa: ASYNC110
                await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=5)
        listener.close()
