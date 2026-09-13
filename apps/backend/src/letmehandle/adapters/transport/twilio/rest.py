"""The five requests this transport makes of the provider's REST API.

Behind a protocol, so the transport's call handling is exercised against a simulated provider
that answers the same requests, and this module is exercised against the same simulator through
an in-memory HTTP transport — the real request building, authentication and error translation,
with no network and no account.

Every failure becomes a `ProviderError` saying whether another attempt could help. Its message
carries the HTTP status and the provider's numeric error code, never a response body, which can
repeat a phone number back.

"Not found" on ending or removing something is not a failure. Teardown runs on paths that
overlap — the caller hanging up while the orchestrator is already cleaning up — and the thing
being ended having ended already is the outcome that was asked for.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, Protocol
from urllib.parse import quote

import httpx

from letmehandle.domain.errors import ProviderError

if TYPE_CHECKING:
    from collections.abc import Sequence

PROVIDER: Final = "twilio"

# The provider's public API origin. Not configuration: there is one, and it is documented.
API_ORIGIN: Final = "https://api.twilio.com"
API_VERSION: Final = "2010-04-01"

REQUEST_TIMEOUT_SECONDS: Final = 10.0

# Progress reported for every dialled leg, so an unanswered, busy or failed dial is heard about.
LEG_EVENTS: Final = ("initiated", "ringing", "answered", "completed")
CONFERENCE_EVENTS: Final = ("start", "end", "join", "leave")

_NOT_FOUND: Final = 404
_RETRYABLE_STATUSES: Final = frozenset({408, 409, 425, 429})
_SERVER_ERROR: Final = 500

type EndStatus = Literal["completed", "canceled"]


@dataclass(frozen=True, slots=True)
class ParticipantRequest:
    """One leg to dial into a conference."""

    to: str
    from_: str
    label: str
    status_callback_url: str
    conference_status_callback_url: str
    timeout_seconds: int
    detect_machine: bool


class TelephonyApi(Protocol):
    """What the transport asks of the provider."""

    async def create_participant(self, conference_name: str, request: ParticipantRequest) -> str:
        """Dial a leg into the named conference, returning the new leg's call identifier."""

    async def update_participant(
        self, conference_sid: str, call_sid: str, *, muted: bool, coach_call_sid: str | None
    ) -> None:
        """Mute or unmute a participant, and make it coach one other participant or none."""

    async def remove_participant(self, conference_sid: str, call_sid: str) -> bool:
        """Take a participant out of a conference. False when it was already gone."""

    async def end_conference(self, conference_sid: str) -> bool:
        """End a conference and every leg in it. False when it had already ended."""

    async def end_call(self, call_sid: str, status: EndStatus) -> bool:
        """Hang up a leg, or cancel one still ringing. False when it had already ended."""

    async def close(self) -> None:
        """Release the connection pool. Safe to call more than once."""


class HttpTelephonyApi:
    """The REST API over HTTPS, authenticated as one account."""

    def __init__(
        self,
        *,
        account_id: str,
        auth_token: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=f"{API_ORIGIN}/{API_VERSION}/Accounts/{quote(account_id, safe='')}",
            auth=(account_id, auth_token),
            timeout=REQUEST_TIMEOUT_SECONDS,
            transport=transport,
        )

    async def create_participant(self, conference_name: str, request: ParticipantRequest) -> str:
        form: list[tuple[str, str]] = [
            ("To", request.to),
            ("From", request.from_),
            ("Label", request.label),
            ("Beep", "false"),
            ("StartConferenceOnEnter", "true"),
            ("EndConferenceOnExit", "false"),
            ("JitterBufferSize", "small"),
            ("ConferenceRecord", "do-not-record"),
            ("Timeout", str(request.timeout_seconds)),
            ("StatusCallback", request.status_callback_url),
            ("StatusCallbackMethod", "POST"),
            *(("StatusCallbackEvent", event) for event in LEG_EVENTS),
            ("ConferenceStatusCallback", request.conference_status_callback_url),
            ("ConferenceStatusCallbackMethod", "POST"),
            *(("ConferenceStatusCallbackEvent", event) for event in CONFERENCE_EVENTS),
        ]
        if request.detect_machine:
            form.append(("MachineDetection", "Enable"))
        body = await self._post(f"/Conferences/{_segment(conference_name)}/Participants.json", form)
        call_sid = body.get("call_sid") if isinstance(body, dict) else None
        if not isinstance(call_sid, str) or not call_sid:
            raise ProviderError(PROVIDER, "a participant was created with no call", retryable=False)
        return call_sid

    async def update_participant(
        self, conference_sid: str, call_sid: str, *, muted: bool, coach_call_sid: str | None
    ) -> None:
        form = [("Muted", _flag(muted)), ("Coaching", _flag(coach_call_sid is not None))]
        if coach_call_sid is not None:
            form.append(("CallSidToCoach", coach_call_sid))
        await self._post(
            f"/Conferences/{_segment(conference_sid)}/Participants/{_segment(call_sid)}.json", form
        )

    async def remove_participant(self, conference_sid: str, call_sid: str) -> bool:
        path = f"/Conferences/{_segment(conference_sid)}/Participants/{_segment(call_sid)}.json"
        return await self._request("DELETE", path, None, missing_is_done=True)

    async def end_conference(self, conference_sid: str) -> bool:
        return await self._request(
            "POST",
            f"/Conferences/{_segment(conference_sid)}.json",
            [("Status", "completed")],
            missing_is_done=True,
        )

    async def end_call(self, call_sid: str, status: EndStatus) -> bool:
        return await self._request(
            "POST", f"/Calls/{_segment(call_sid)}.json", [("Status", status)], missing_is_done=True
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _post(self, path: str, form: Sequence[tuple[str, str]]) -> object:
        response = await self._send("POST", path, form)
        _raise_for(response)
        try:
            return response.json()
        except ValueError:
            raise ProviderError(PROVIDER, "the API answered with no JSON", retryable=True) from None

    async def _request(
        self,
        method: str,
        path: str,
        form: Sequence[tuple[str, str]] | None,
        *,
        missing_is_done: bool,
    ) -> bool:
        response = await self._send(method, path, form)
        if missing_is_done and response.status_code == _NOT_FOUND:
            return False
        _raise_for(response)
        return True

    async def _send(
        self, method: str, path: str, form: Sequence[tuple[str, str]] | None
    ) -> httpx.Response:
        try:
            return await self._client.request(method, path, data=_form(form))
        except httpx.TimeoutException:
            raise ProviderError(
                PROVIDER, "the API did not answer in time", retryable=True
            ) from None
        except httpx.TransportError as error:
            raise ProviderError(
                PROVIDER, f"the API could not be reached: {type(error).__name__}", retryable=True
            ) from None


def _form(form: Sequence[tuple[str, str]] | None) -> dict[str, list[str]] | None:
    """httpx takes repeated fields as a list per name, and keeps their order."""
    if form is None:
        return None
    fields: dict[str, list[str]] = {}
    for name, value in form:
        fields.setdefault(name, []).append(value)
    return fields


def _raise_for(response: httpx.Response) -> None:
    status = response.status_code
    if status < 300:
        return
    code = _error_code(response)
    raise ProviderError(
        PROVIDER,
        f"the API refused the request with HTTP {status}"
        + (f" and error {code}" if code is not None else ""),
        retryable=status in _RETRYABLE_STATUSES or status >= _SERVER_ERROR,
    )


def _error_code(response: httpx.Response) -> int | None:
    try:
        body = response.json()
    except ValueError:
        return None
    code = body.get("code") if isinstance(body, dict) else None
    return code if isinstance(code, int) and not isinstance(code, bool) else None


def _segment(value: str) -> str:
    return quote(value, safe="")


def _flag(value: bool) -> str:
    return "true" if value else "false"
