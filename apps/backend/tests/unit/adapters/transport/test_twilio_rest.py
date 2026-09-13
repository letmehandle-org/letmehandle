"""The REST client: what it sends, how it authenticates, and what each failure becomes."""

from __future__ import annotations

import base64
from urllib.parse import parse_qsl

import httpx
import pytest

from letmehandle.adapters.transport.twilio.rest import HttpTelephonyApi, ParticipantRequest
from letmehandle.domain.errors import ProviderError

ACCOUNT = "account-for-tests"
TOKEN = "token-for-tests"


class Recorder:
    def __init__(self, response: httpx.Response | Exception) -> None:
        self.response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def form(self) -> list[tuple[str, str]]:
        return parse_qsl(self.requests[-1].content.decode(), keep_blank_values=True)


def api(recorder: Recorder) -> HttpTelephonyApi:
    return HttpTelephonyApi(
        account_id=ACCOUNT, auth_token=TOKEN, transport=httpx.MockTransport(recorder)
    )


REQUEST = ParticipantRequest(
    to="+12025550143",
    from_="+12025550100",
    label="user-2",
    status_callback_url="https://calls.example.com/leg?call=c&leg=user-2",
    conference_status_callback_url="https://calls.example.com/conference?call=c",
    timeout_seconds=30,
    detect_machine=True,
)


async def test_a_participant_is_dialled_into_the_named_conference_with_every_callback() -> None:
    recorder = Recorder(httpx.Response(201, json={"call_sid": "CAsim-9"}))
    client = api(recorder)
    assert await client.create_participant("call-CAsim 1", REQUEST) == "CAsim-9"
    request = recorder.requests[-1]
    assert request.method == "POST"
    assert request.url.raw_path.decode() == (
        f"/2010-04-01/Accounts/{ACCOUNT}/Conferences/call-CAsim%201/Participants.json"
    )
    expected = "Basic " + base64.b64encode(f"{ACCOUNT}:{TOKEN}".encode()).decode()
    assert request.headers["Authorization"] == expected
    form = recorder.form()
    fields = dict(form)
    assert fields["To"] == "+12025550143"
    assert fields["From"] == "+12025550100"
    assert fields["Label"] == "user-2"
    assert fields["Beep"] == "false"
    assert fields["EndConferenceOnExit"] == "false"
    assert fields["JitterBufferSize"] == "small"
    assert fields["ConferenceRecord"] == "do-not-record"
    assert fields["MachineDetection"] == "Enable"
    assert fields["Timeout"] == "30"
    assert [value for name, value in form if name == "StatusCallbackEvent"] == [
        "initiated",
        "ringing",
        "answered",
        "completed",
    ]
    assert [value for name, value in form if name == "ConferenceStatusCallbackEvent"] == [
        "start",
        "end",
        "join",
        "leave",
    ]
    await client.close()


async def test_machine_detection_is_only_asked_for_when_wanted() -> None:
    recorder = Recorder(httpx.Response(201, json={"call_sid": "CAsim-9"}))
    client = api(recorder)
    await client.create_participant(
        "c",
        ParticipantRequest(
            to="app:x",
            from_="+12025550100",
            label="assistant-1",
            status_callback_url="u",
            conference_status_callback_url="u",
            timeout_seconds=15,
            detect_machine=False,
        ),
    )
    assert "MachineDetection" not in dict(recorder.form())
    await client.close()


@pytest.mark.parametrize(
    "response",
    [httpx.Response(201, json={"label": "x"}), httpx.Response(201, json=["x"])],
)
async def test_a_participant_created_with_no_call_is_a_failure(response: httpx.Response) -> None:
    client = api(Recorder(response))
    with pytest.raises(ProviderError, match="no call") as failure:
        await client.create_participant("c", REQUEST)
    assert not failure.value.retryable


async def test_an_answer_that_is_not_json_is_worth_another_attempt() -> None:
    client = api(Recorder(httpx.Response(201, text="<html>")))
    with pytest.raises(ProviderError, match="no JSON") as failure:
        await client.create_participant("c", REQUEST)
    assert failure.value.retryable


async def test_muting_and_coaching_are_one_update() -> None:
    recorder = Recorder(httpx.Response(200, json={}))
    client = api(recorder)
    await client.update_participant("CFsim-1", "CAsim-2", muted=False, coach_call_sid="CAsim-3")
    assert recorder.requests[-1].url.path.endswith("/Conferences/CFsim-1/Participants/CAsim-2.json")
    assert recorder.form() == [
        ("Muted", "false"),
        ("Coaching", "true"),
        ("CallSidToCoach", "CAsim-3"),
    ]
    await client.update_participant("CFsim-1", "CAsim-2", muted=True, coach_call_sid=None)
    assert recorder.form() == [("Muted", "true"), ("Coaching", "false")]


@pytest.mark.parametrize(("status", "expected"), [(204, True), (404, False)])
async def test_removing_a_participant_already_gone_is_not_a_failure(
    status: int, expected: bool
) -> None:
    recorder = Recorder(httpx.Response(status))
    client = api(recorder)
    assert await client.remove_participant("CFsim-1", "CAsim-2") is expected
    assert recorder.requests[-1].method == "DELETE"


@pytest.mark.parametrize(("status", "expected"), [(200, True), (404, False)])
async def test_ending_a_conference_or_a_call_already_ended_is_not_a_failure(
    status: int, expected: bool
) -> None:
    recorder = Recorder(httpx.Response(status, json={}))
    client = api(recorder)
    assert await client.end_conference("CFsim-1") is expected
    assert recorder.form() == [("Status", "completed")]
    assert await client.end_call("CAsim-2", "canceled") is expected
    assert recorder.requests[-1].url.path.endswith("/Calls/CAsim-2.json")
    assert recorder.form() == [("Status", "canceled")]


async def test_conferences_are_ended_by_name_once_looked_up() -> None:
    listing = {"conferences": [{"sid": "CFsim-1"}, {"sid": "CFsim-2"}, {"friendly_name": "x"}]}
    requests: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=listing)
        # The second ended on its own between the listing and the request.
        return httpx.Response(404 if "CFsim-2" in request.url.path else 200, json={})

    client = HttpTelephonyApi(
        account_id=ACCOUNT, auth_token=TOKEN, transport=httpx.MockTransport(answer)
    )
    assert await client.end_conferences_named("call-CAsim 1") == 1
    lookup = requests[0]
    assert lookup.url.path.endswith("/Conferences.json")
    assert dict(lookup.url.params) == {"FriendlyName": "call-CAsim 1", "Status": "in-progress"}
    assert [request.url.path.rsplit("/", 1)[-1] for request in requests[1:]] == [
        "CFsim-1.json",
        "CFsim-2.json",
    ]
    await client.close()


@pytest.mark.parametrize(
    ("response", "retryable"),
    [
        (httpx.Response(200, text="<html>"), True),
        (httpx.Response(200, json={"conferences": "none"}), False),
        (httpx.Response(200, json=["x"]), False),
        (httpx.Response(503, json={}), True),
    ],
)
async def test_a_conference_listing_that_cannot_be_read_is_a_failure(
    response: httpx.Response, retryable: bool
) -> None:
    client = api(Recorder(response))
    with pytest.raises(ProviderError) as failure:
        await client.end_conferences_named("call-c")
    assert failure.value.retryable is retryable


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(400, False), (401, False), (403, False), (429, True), (409, True), (500, True), (503, True)],
)
async def test_a_refusal_says_whether_to_try_again_and_never_repeats_the_body(
    status: int, retryable: bool
) -> None:
    body = {"code": 21217, "message": "The number +12025550143 is not valid"}
    client = api(Recorder(httpx.Response(status, json=body)))
    with pytest.raises(ProviderError) as failure:
        await client.update_participant("c", "p", muted=True, coach_call_sid=None)
    assert failure.value.retryable is retryable
    assert f"HTTP {status}" in str(failure.value)
    assert "21217" in str(failure.value)
    assert "+1202" not in str(failure.value)


@pytest.mark.parametrize(
    "body", [httpx.Response(400, text="nope"), httpx.Response(400, json={"code": True})]
)
async def test_a_refusal_without_a_readable_code_still_names_the_status(
    body: httpx.Response,
) -> None:
    client = api(Recorder(body))
    with pytest.raises(ProviderError, match=r"HTTP 400$"):
        await client.end_call("c", "completed")


async def test_a_timeout_is_worth_another_attempt() -> None:
    client = api(Recorder(httpx.ReadTimeout("slow")))
    with pytest.raises(ProviderError, match="in time") as failure:
        await client.end_conference("c")
    assert failure.value.retryable


async def test_an_unreachable_api_is_worth_another_attempt() -> None:
    client = api(Recorder(httpx.ConnectError("refused")))
    with pytest.raises(ProviderError, match="ConnectError") as failure:
        await client.remove_participant("c", "p")
    assert failure.value.retryable
