"""The provider's REST API; failures carry no body, and not found on ending is done."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, Protocol
from urllib.parse import quote

import httpx

from letmehandle.adapters.transport.twilio.callbacks import CONFERENCE_EVENTS
from letmehandle.domain.errors import DeliveryUncertainError, ProviderError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

PROVIDER: Final = "twilio"

# The provider's public API origin.
API_ORIGIN: Final = "https://api.twilio.com"
API_VERSION: Final = "2010-04-01"

REQUEST_TIMEOUT_SECONDS: Final = 10.0

# Progress reported for every dialled leg, so an unanswered, busy or failed dial is heard about.
LEG_EVENTS: Final = ("initiated", "ringing", "answered", "completed")

_NOT_FOUND: Final = 404
_RETRYABLE_STATUSES: Final = frozenset({408, 409, 425, 429})
_SERVER_ERROR: Final = 500

type EndStatus = Literal["completed", "canceled"]

# The statuses of a leg that is not over, and how a leg in each is ended.
_LIVE_LEG_STATUSES: Final[tuple[tuple[str, EndStatus], ...]] = (
    ("queued", "canceled"),
    ("ringing", "canceled"),
    ("in-progress", "completed"),
)


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


@dataclass(frozen=True, slots=True)
class CallRecord:
    """The number a call reached and the line that forwarded it, unparsed; either may be absent."""

    to: str | None
    forwarded_from: str | None


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

    async def end_conferences_named(self, conference_name: str) -> int:
        """End every conference in progress under this name, saying how many there were."""

    async def find_call(self, call_sid: str) -> CallRecord | None:
        """What the provider knows of a call, or None when it knows nothing of it."""

    async def end_calls_between(self, from_: str, to: str) -> int:
        """End every leg from one number to another that is not over, saying how many there were."""

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
        self._client = account_client(
            account_id=account_id, auth_token=auth_token, transport=transport
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

    async def end_conferences_named(self, conference_name: str) -> int:
        # A conference is ended by identifier, so one known only by name is looked up first.
        response = await self._send(
            "GET",
            "/Conferences.json",
            None,
            params={"FriendlyName": conference_name, "Status": "in-progress"},
        )
        raise_for(response)
        ended = 0
        for conference_sid in _sids(_json(response), "conferences"):
            if await self.end_conference(conference_sid):
                ended += 1
        return ended

    async def find_call(self, call_sid: str) -> CallRecord | None:
        response = await self._send("GET", f"/Calls/{_segment(call_sid)}.json", None)
        if response.status_code == _NOT_FOUND:
            return None
        raise_for(response)
        body = _json(response)
        if not isinstance(body, dict):
            raise ProviderError(PROVIDER, "the API described no call", retryable=False)
        return CallRecord(
            to=_text(body.get("to")), forwarded_from=_text(body.get("forwarded_from"))
        )

    async def end_calls_between(self, from_: str, to: str) -> int:
        # The API filters by one status at a time; each leg is ended by its identifier.
        ended = 0
        for status, end_status in _LIVE_LEG_STATUSES:
            response = await self._send(
                "GET", "/Calls.json", None, params={"From": from_, "To": to, "Status": status}
            )
            raise_for(response)
            for call_sid in _sids(_json(response), "calls"):
                if await self.end_call(call_sid, end_status):
                    ended += 1
        return ended

    async def close(self) -> None:
        await self._client.aclose()

    async def _post(self, path: str, form: Sequence[tuple[str, str]]) -> object:
        response = await self._send("POST", path, form)
        raise_for(response)
        return _json(response)

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
        raise_for(response)
        return True

    async def _send(
        self,
        method: str,
        path: str,
        form: Sequence[tuple[str, str]] | None,
        *,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        return await send(self._client, method, path, _form(form), params=params)


def account_client(
    *, account_id: str, auth_token: str, transport: httpx.AsyncBaseTransport | None
) -> httpx.AsyncClient:
    """A client for one account's resources, shared by everything calling this API as an account."""
    return httpx.AsyncClient(
        base_url=f"{API_ORIGIN}/{API_VERSION}/Accounts/{quote(account_id, safe='')}",
        auth=(account_id, auth_token),
        timeout=REQUEST_TIMEOUT_SECONDS,
        transport=transport,
    )


async def send(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    form: Mapping[str, list[str]] | None,
    *,
    params: dict[str, str] | None = None,
) -> httpx.Response:
    """One request, with a network failure or a timeout as a `ProviderError` worth retrying."""
    try:
        return await client.request(method, path, data=form, params=params)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as error:
        # Nothing left this process, so nothing can have been done.
        raise ProviderError(
            PROVIDER, f"the API could not be reached: {type(error).__name__}", retryable=True
        ) from None
    except httpx.TimeoutException:
        raise DeliveryUncertainError(PROVIDER, "the API did not answer in time") from None
    except httpx.TransportError as error:
        raise DeliveryUncertainError(
            PROVIDER, f"the connection failed after sending: {type(error).__name__}"
        ) from None


def _form(form: Sequence[tuple[str, str]] | None) -> dict[str, list[str]] | None:
    """httpx takes repeated fields as a list per name, and keeps their order."""
    if form is None:
        return None
    fields: dict[str, list[str]] = {}
    for name, value in form:
        fields.setdefault(name, []).append(value)
    return fields


def raise_for(response: httpx.Response) -> None:
    """Raise a `ProviderError` for a refusal, saying whether another attempt could help."""
    status = response.status_code
    if status < 300:
        return
    code = error_code(response)
    raise ProviderError(
        PROVIDER,
        f"the API refused the request with HTTP {status}"
        + (f" and error {code}" if code is not None else ""),
        retryable=status in _RETRYABLE_STATUSES or status >= _SERVER_ERROR,
    )


def _json(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError:
        raise ProviderError(PROVIDER, "the API answered with no JSON", retryable=True) from None


def _sids(body: object, listing: str) -> list[str]:
    """The identifiers in a listing of `listing`, skipping any entry without one."""
    entries = body.get(listing) if isinstance(body, dict) else None
    if not isinstance(entries, list):
        raise ProviderError(PROVIDER, f"the API listed no {listing}", retryable=False)
    return [
        entry["sid"]
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("sid"), str)
    ]


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def error_code(response: httpx.Response) -> int | None:
    """The provider's numeric error code, when the response carries one."""
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
