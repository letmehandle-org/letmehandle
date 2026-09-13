# ruff: noqa: T201, E402 - a terminal tool whose output is the point; imports follow the path
"""Rehearse whole calls on the configured speech service and model, only telephony simulated.

The application runs on loopback exactly as the end-to-end scenarios run it — its own lifespan and
orchestrator, storage in PostgreSQL, the simulated telephony provider calling it back over signed
HTTP and a real media websocket — except that nothing here is scripted on the product's side: the
assistant speaks through the configured speech service, and the agent and the summariser think
with the configured model. The caller's side is a script of spoken lines, synthesised by the same
speech service and fed into the call's media stream at the pace a phone line carries it.

    cd apps/backend
    uv run python ../../scripts/live_rehearsal.py --env-file ../../.env

It needs a PostgreSQL server (a throwaway database is created on it and dropped afterwards), and
`SPEECH_*` and `LLM_*` in the env file. Nothing else is read from that file, nothing read from it
is printed, and every other secret — the signing key, the transcript keys, the diagnostics token —
is generated for the run and forgotten with it.

Each run is two calls and costs a little speech and model usage. Nothing is recorded: the caller's
audio exists in memory until it is sent, and the assistant's is counted as it arrives, not kept
(D-013). What is printed is structure, the scripted lines, and what the product stored about them.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import secrets
import statistics
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "apps" / "backend"

# The backend's sources, and its test support, which holds the simulated telephony provider.
sys.path.insert(0, str(BACKEND / "src"))
sys.path.insert(0, str(BACKEND))

import httpx
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from letmehandle import bootstrap
from letmehandle.adapters.database.models import Base
from letmehandle.application.calls.summary_checks import words
from letmehandle.bootstrap import build_call_transport, build_observability, build_reported_calls
from letmehandle.config.settings import (
    SpeechProviderName,
    TelephonyProviderName,
    parse_voice_catalogue,
)
from letmehandle.domain.models.call_state import CallState
from letmehandle.domain.ports.notification import DevicePlatform
from letmehandle.main import create_app
from tests.contracts.fakes import RecordingNotificationProvider
from tests.e2e.app_client import AppClient, call_handling
from tests.e2e.harness import CALLER, USERS_LINE
from tests.support.config import make_settings
from tests.support.simulated_twilio import (
    OUR_NUMBER,
    PUBLIC_BASE_URL,
    SIMULATED_ACCOUNT,
    SIMULATED_APP,
    SIMULATED_TOKEN,
    Answering,
    SimulatedLeg,
    SimulatedTwilio,
    serving,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Sequence

    from fastapi import FastAPI

    from letmehandle.application.orchestration.orchestrator import CallOrchestrator
    from letmehandle.config.settings import Settings
    from letmehandle.domain.ports.clock import Clock
    from letmehandle.domain.ports.notification import NotificationProvider
    from tests.e2e.app_client import Account, Json

# A phone line carries 8 kHz mu-law in 20 ms frames: 160 bytes each.
_FRAME_BYTES: Final = 160
_FRAME_SECONDS: Final = 0.02
_BYTES_PER_SECOND: Final = 8_000
# Mu-law's zero: what an open line sends between words.
_SILENCE: Final = b"\xff" * _FRAME_BYTES

# How long the assistant has been quiet before the caller takes it as their turn. Roughly a person's
# pause; shorter, and the caller talks over a reply the service delivers in two parts.
_TURN_PAUSE_SECONDS: Final = 1.5
# How long the caller waits for the assistant to say anything at all before speaking regardless.
_TURN_PATIENCE_SECONDS: Final = 20.0
# How long the caller and the user talk once the user has joined, before the caller hangs up.
_WITH_THE_USER_SECONDS: Final = 3.0
# How long a whole call may take to be answered, streamed, ended and summarised. A real model is
# slower than loopback by orders of magnitude, and a summary is written after the call ends.
_CALL_PATIENCE_SECONDS: Final = 90.0

_ENV_PREFIXES: Final = ("SPEECH_", "LLM_")


@dataclass(frozen=True, slots=True)
class Scenario:
    """One call: who rings, what they say, and how the user's phone behaves if it is rung."""

    name: str
    call_id: str
    lines: tuple[str, ...]
    users_phone: Answering
    expects_escalation: bool


SCENARIOS: Final = (
    Scenario(
        name="A: a routine caller the assistant handles",
        call_id="CAsim-rehearsal-routine",
        lines=(
            "Hello, I'm calling from the dental clinic to confirm your appointment tomorrow at 10.",
            "No, that's everything. Thank you, goodbye.",
        ),
        users_phone=Answering.KEEPS_RINGING,
        expects_escalation=False,
    ),
    Scenario(
        name="B: a caller who urgently needs the user",
        call_id="CAsim-rehearsal-urgent",
        lines=(
            "Hi, this is Sam, her neighbour. I need to speak to her right now, it's urgent.",
            "Water is pouring through her ceiling from the flat upstairs. Please put her on.",
        ),
        users_phone=Answering.ANSWERS,
        expects_escalation=True,
    ),
)


# ----------------------------------------------------------------------------------- configuration


def read_env(path: Path) -> dict[str, str]:
    """The speech and model variables from an env file, and nothing else from it."""
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.startswith(_ENV_PREFIXES) and value:
            values[name] = value.strip().strip('"').strip("'")
    return values


def rehearsal_settings(
    env: dict[str, str], database_url: str, diagnostics_token: str, log_level: str
) -> Settings:
    """A deployment on the simulated telephony account and the configured speech service and model.

    Every key is made for this run: nothing the rehearsal stores outlives it, so nothing needs a
    key that does.
    """
    return make_settings(
        database_url=database_url,
        log_level=log_level,
        auth_signing_key=secrets.token_urlsafe(48),
        transcript_encryption_keys=f"rehearsal:{base64.b64encode(secrets.token_bytes(32)).decode()}",
        diagnostics_token=diagnostics_token,
        telephony_provider=TelephonyProviderName.TWILIO,
        telephony_account_id=SIMULATED_ACCOUNT,
        telephony_auth_token=SIMULATED_TOKEN,
        telephony_numbers=(OUR_NUMBER,),
        telephony_app_id=SIMULATED_APP,
        telephony_webhook_base_url=PUBLIC_BASE_URL,
        speech_provider=SpeechProviderName(env["SPEECH_PROVIDER"]),
        speech_endpoint_url=env["SPEECH_ENDPOINT_URL"],
        speech_model=env.get("SPEECH_MODEL"),
        speech_agent_id=env.get("SPEECH_AGENT_ID"),
        speech_api_key=env.get("SPEECH_API_KEY"),
        speech_voices=parse_voice_catalogue(env["SPEECH_VOICES"]),
        speech_default_voice=env["SPEECH_DEFAULT_VOICE"],
        llm_base_url=env["LLM_BASE_URL"],
        llm_api_key=env.get("LLM_API_KEY"),
        llm_model=env["LLM_MODEL"],
        llm_headers=env.get("LLM_HEADERS", ""),
        llm_timeout_seconds=float(env.get("LLM_TIMEOUT_SECONDS", "20")),
    )


@asynccontextmanager
async def throwaway_database(server_url: str) -> AsyncIterator[str]:
    """A database created with the schema for this run, and dropped however the run ends."""
    name = f"letmehandle_rehearsal_{secrets.token_hex(4)}"
    server = create_async_engine(server_url, isolation_level="AUTOCOMMIT")
    try:
        async with server.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{name}"'))
        own = make_url(server_url).set(database=name).render_as_string(hide_password=False)
        engine = create_async_engine(own)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        finally:
            await engine.dispose()
        yield own
    finally:
        async with server.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await server.dispose()


@dataclass(frozen=True, slots=True)
class Pushes:
    """A recording push provider per platform, standing where credentials and a platform would."""

    ios: RecordingNotificationProvider = field(
        default_factory=lambda: RecordingNotificationProvider(DevicePlatform.IOS)
    )
    android: RecordingNotificationProvider = field(
        default_factory=lambda: RecordingNotificationProvider(DevicePlatform.ANDROID)
    )

    def install(self) -> None:
        """Put these where the application chooses its push providers, as the scenarios do."""

        def providers(settings: Settings, *, clock: Clock) -> tuple[NotificationProvider, ...]:
            return (self.ios, self.android)

        bootstrap.build_notification_providers = providers


# ------------------------------------------------------------------------------ the caller's voice


class CallerVoice:
    """Speaks the caller's lines with the speech service's text-to-speech, as a phone line's audio.

    Asked for in the line's own format, so nothing is converted, and held in memory only.
    """

    def __init__(self, env: dict[str, str]) -> None:
        endpoint = urlsplit(env["SPEECH_ENDPOINT_URL"])
        self._base = f"https://{endpoint.netloc}"
        self._key = env.get("SPEECH_API_KEY", "")
        voices = parse_voice_catalogue(env["SPEECH_VOICES"])
        default = env["SPEECH_DEFAULT_VOICE"]
        # A voice other than the assistant's where the catalogue has one, so the two are told apart.
        self._voice = next((each.id for each in voices if each.id != default), default)

    async def synthesise(self, line: str) -> bytes:
        async with httpx.AsyncClient(base_url=self._base, timeout=30.0) as client:
            response = await client.post(
                f"/v1/text-to-speech/{self._voice}",
                params={"output_format": "ulaw_8000"},
                headers={"xi-api-key": self._key},
                json={"text": line},
            )
        if response.status_code != 200:
            # The status alone: a refusal's body can echo what was sent with it.
            raise SystemExit(
                f"text-to-speech refused the caller's line: HTTP {response.status_code}"
            )
        return response.content


# --------------------------------------------------------------------------------- the phone line


class CallerLine:
    """The caller's side of the media stream: speech when there is some, silence otherwise.

    Always sending, at the pace of the line, because a speech service decides a turn has ended by
    hearing the silence after it; a stream that simply stops is a caller who has gone.
    """

    def __init__(self, provider: SimulatedTwilio, call_id: str) -> None:
        self._provider = provider
        self._call_id = call_id
        self._speech: asyncio.Queue[tuple[bytes, asyncio.Event]] = asyncio.Queue()
        self.finished_speaking_at = 0.0

    async def say(self, audio: bytes) -> None:
        """Speak `audio` into the call, returning once the last of it has been sent."""
        done = asyncio.Event()
        await self._speech.put((audio, done))
        await done.wait()

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        next_frame = loop.time()
        pending = b""
        done: asyncio.Event | None = None
        while True:
            if not pending and not self._speech.empty():
                pending, done = self._speech.get_nowait()
            if pending:
                frame, pending = pending[:_FRAME_BYTES], pending[_FRAME_BYTES:]
                frame = frame.ljust(_FRAME_BYTES, _SILENCE[:1])
            else:
                frame = _SILENCE
            await self._provider.send_caller_audio(self._call_id, frame)
            if not pending and done is not None:
                self.finished_speaking_at = loop.time()
                done.set()
                done = None
            # A line that fell behind is late, not faster: the debt is dropped rather than sent as a
            # burst, which a speech service would hear as speech sped up.
            next_frame = max(next_frame + _FRAME_SECONDS, loop.time())
            await asyncio.sleep(next_frame - loop.time())


class AssistantEar:
    """Counts the assistant's audio arriving on the call, and when it would finish playing."""

    def __init__(self, leg: SimulatedLeg) -> None:
        self._leg = leg
        self.first_audio_at: float | None = None
        self.bytes_heard = 0
        self._playing_until = 0.0
        # Each stretch of speech: when its first audio arrived, and how long it plays for.
        self.bursts: list[list[float]] = []
        self._seen = 0

    @property
    def seconds_heard(self) -> float:
        return self.bytes_heard / _BYTES_PER_SECOND

    @property
    def frames_heard(self) -> int:
        return self._seen

    def first_audio_after(self, moment: float) -> float | None:
        """How long after `moment` the assistant's next audio arrived, if it has."""
        return next((start - moment for start, _ in self.bursts if start >= moment), None)

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            arrived = self._leg.sent_to_call[self._seen :]
            if arrived:
                now = loop.time()
                self._seen += len(arrived)
                size = sum(len(each) for each in arrived)
                self.bytes_heard += size
                if self.first_audio_at is None:
                    self.first_audio_at = now
                # The start of each burst, which is what a response latency is measured to.
                if now > self._playing_until:
                    self.bursts.append([now, 0.0])
                self.bursts[-1][1] += size / _BYTES_PER_SECOND
                self._playing_until = max(self._playing_until, now) + size / _BYTES_PER_SECOND
            # The leg keeps a list, not an event, so it is read at the pace of the line.
            await asyncio.sleep(_FRAME_SECONDS)

    async def quiet(self) -> None:
        """Wait until the assistant has finished speaking and paused, or has long said nothing."""
        loop = asyncio.get_running_loop()
        waited_from = loop.time()
        while True:
            now = loop.time()
            silent_for = now - max(self._playing_until, waited_from)
            if self._playing_until > waited_from and silent_for >= _TURN_PAUSE_SECONDS:
                return
            if self._playing_until <= waited_from and now - waited_from >= _TURN_PATIENCE_SECONDS:
                return
            await asyncio.sleep(_FRAME_SECONDS)


class StateWatch:
    """Every state the call's run moves through while it is live, and when."""

    def __init__(self, orchestrator: CallOrchestrator, call_id: str, started_at: float) -> None:
        self._orchestrator = orchestrator
        self._call_id = call_id
        self._started_at = started_at
        self.seen: list[tuple[float, CallState]] = []

    def reached(self, state: CallState) -> bool:
        return any(each is state for _, each in self.seen)

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            standing = next(
                (
                    each
                    for each in self._orchestrator.standings()
                    if each.call_id.value == self._call_id
                ),
                None,
            )
            if standing is not None and (not self.seen or self.seen[-1][1] is not standing.state):
                self.seen.append((loop.time() - self._started_at, standing.state))
            await asyncio.sleep(0.05)


# ------------------------------------------------------------------------------------ a rehearsal


@dataclass(slots=True)
class Result:
    """What one rehearsed call showed, for the report."""

    scenario: Scenario
    states: list[tuple[float, CallState]] = field(default_factory=list)
    stream_up_seconds: float | None = None
    first_audio_seconds: float | None = None
    replies: list[float | None] = field(default_factory=list)
    frames_heard: int = 0
    seconds_heard: float = 0.0
    clears: int = 0
    bursts: list[tuple[float, float]] = field(default_factory=list)
    caller_spoke: list[tuple[float, float]] = field(default_factory=list)
    detail: Json | None = None
    # Lines stored as the caller's, and as the assistant's.
    transcript_lines: tuple[int, int] = (0, 0)
    recognised: float = 0.0
    escalation: Json | None = None
    pushes: list[str] = field(default_factory=list)
    timeline: Json | None = None
    hung_up_by: str = ""


async def rehearse(
    app: FastAPI,
    api: AppClient,
    provider: SimulatedTwilio,
    account: Account,
    voice: CallerVoice,
    pushes: Pushes,
    scenario: Scenario,
    diagnostics: dict[str, str],
) -> Result:
    """Place the scenario's call, speak its lines, let it end, and read back what was stored."""
    loop = asyncio.get_running_loop()
    orchestrator: CallOrchestrator = app.state.orchestrator
    result = Result(scenario)
    # Synthesised before the call, so the time text-to-speech takes is not counted as the caller's
    # pauses, and the assistant is not left waiting on it.
    spoken = [await voice.synthesise(line) for line in scenario.lines]
    provider.answering[USERS_LINE] = scenario.users_phone
    pushed_before = {each.platform: len(each.sent) for each in (pushes.ios, pushes.android)}

    placed_at = loop.time()
    watch = StateWatch(orchestrator, scenario.call_id, placed_at)
    async with asyncio.TaskGroup() as group:
        watching = group.create_task(watch.run())
        await provider.place_call(scenario.call_id, CALLER, forwarded_from=account.number)
        leg = await _assistant_leg(provider, scenario.call_id)
        stream_up = loop.time()
        result.stream_up_seconds = stream_up - placed_at
        ear = AssistantEar(leg)
        line = CallerLine(provider, scenario.call_id)
        listening = group.create_task(ear.run())
        speaking = group.create_task(line.run())

        async def speak(lines: Sequence[str], audio: Sequence[bytes]) -> None:
            for text_line, each in zip(lines, audio, strict=True):
                if provider.conference_of(scenario.call_id).ended:
                    return
                print(f"  caller: {text_line}")
                started = loop.time()
                await line.say(each)
                said_at = line.finished_speaking_at
                result.caller_spoke.append((started - stream_up, said_at - started))
                await ear.quiet()
                result.replies.append(ear.first_audio_after(said_at))

        try:
            await ear.quiet()
            await speak(scenario.lines, spoken)
            if scenario.expects_escalation:
                await _until(lambda: watch.reached(CallState.HUMAN_JOINED), _TURN_PATIENCE_SECONDS)
                # The user on the call for a moment, as a person picking up would be, before the
                # caller, satisfied, hangs up.
                await asyncio.sleep(_WITH_THE_USER_SECONDS)
        finally:
            speaking.cancel()
            listening.cancel()
        if provider.conference_of(scenario.call_id).ended:
            result.hung_up_by = "the product"
        else:
            result.hung_up_by = "the caller"
            await provider.caller_hangs_up(scenario.call_id)
        result.detail = await _ended(api, account, scenario.call_id)
        watching.cancel()

    result.states = watch.seen
    result.first_audio_seconds = (
        None if ear.first_audio_at is None else ear.first_audio_at - stream_up
    )
    result.frames_heard = ear.frames_heard
    result.seconds_heard = ear.seconds_heard
    result.clears = leg.clears
    result.bursts = [(start - stream_up, seconds) for start, seconds in ear.bursts]
    transcript = await api.http.get(
        f"/v1/calls/{scenario.call_id}/transcript", headers=account.headers
    )
    entries = transcript.json()["entries"] if transcript.status_code == 200 else []
    by_caller = [each["text"] for each in entries if each["speaker"] == "caller"]
    result.transcript_lines = (len(by_caller), len(entries) - len(by_caller))
    result.recognised = recognised(scenario.lines, by_caller)
    result.escalation = await api.escalation(account, scenario.call_id)
    result.pushes = [
        f"{provider_.platform.value}: title={note.title!r} caller={note.caller_label!r} "
        f"reason={note.data.get('reason')!r} body={note.body!r}"
        for provider_ in (pushes.ios, pushes.android)
        for _, note in provider_.sent[pushed_before.get(provider_.platform, 0) :]
    ]
    timeline = await api.http.get(f"/diagnostics/calls/{scenario.call_id}", headers=diagnostics)
    result.timeline = timeline.json() if timeline.status_code == 200 else None
    return result


def recognised(scripted: Sequence[str], transcribed: Sequence[str]) -> float:
    """The share of the script's words that appear anywhere in what was transcribed.

    A measure of whether the caller was heard, computed here so that nothing transcribed is printed.
    """
    wanted = [word for line in scripted for word in words(line)]
    heard = {word for line in transcribed for word in words(line)}
    return sum(word in heard for word in wanted) / len(wanted) if wanted else 0.0


async def _assistant_leg(provider: SimulatedTwilio, call_id: str) -> SimulatedLeg:
    """The assistant's leg once its media socket is open, allowing a real speech service to open."""
    async with asyncio.timeout(_TURN_PATIENCE_SECONDS):
        while True:
            with contextlib.suppress(TimeoutError, KeyError, AssertionError):
                return await provider.assistant_of(call_id)


async def _until(condition: Callable[[], bool], seconds: float) -> bool:
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(seconds):
            # A watcher's list, not an event, so it is polled.
            while not condition():  # noqa: ASYNC110
                await asyncio.sleep(0.05)
            return True
    return False


async def _ended(api: AppClient, account: Account, call_id: str) -> Json | None:
    """The call's detail once its summary is written, or what there is when patience runs out."""
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(_CALL_PATIENCE_SECONDS):
            while True:
                detail = await api.call(account, call_id)
                if detail is not None and detail["outcome"] is not None:
                    return detail
                await asyncio.sleep(0.2)
    return await api.call(account, call_id)


# --------------------------------------------------------------------------------------- the report


def report(result: Result) -> None:
    print(f"\n== {result.scenario.name}")
    print("  states: " + " -> ".join(f"{state.value}@{at:.1f}s" for at, state in result.states))
    print(f"  media stream up {_seconds(result.stream_up_seconds)} after the call was placed")
    spoke = "yes" if result.frames_heard else "NO"
    print(
        f"  assistant spoke: {spoke} — {result.frames_heard} frames, "
        f"{result.seconds_heard:.1f}s of audio; "
        f"first audio {_seconds(result.first_audio_seconds)} after the stream opened; "
        f"{result.clears} clears"
    )
    print(
        "  the assistant's speech (from the stream opening): "
        + ", ".join(f"{seconds:.1f}s at {start:.1f}s" for start, seconds in result.bursts)
    )
    print(
        "  the caller's lines: "
        + ", ".join(f"{seconds:.1f}s at {start:.1f}s" for start, seconds in result.caller_spoke)
    )
    print("  reply after each caller line: " + ", ".join(_seconds(each) for each in result.replies))
    print(f"  call ended by {result.hung_up_by}")
    detail = result.detail or {}
    print(
        f"  stored: status={detail.get('status')} outcome={detail.get('outcome')} "
        f"handling={detail.get('handling')} intent={detail.get('intent')} "
        f"importance={detail.get('importance')} human_joined={detail.get('human_joined')}"
    )
    print(f"  headline: {detail.get('headline')!r}")
    caller_lines, assistant_lines = result.transcript_lines
    print(
        f"  transcript lines stored: {caller_lines} the caller's, {assistant_lines} the "
        f"assistant's; {result.recognised:.0%} of the scripted words were transcribed"
    )
    if result.escalation is not None:
        shown = {
            key: result.escalation.get(key)
            for key in ("status", "reason", "delivery", "established")
        }
        print(f"  escalation: {shown}")
    else:
        print("  escalation: none")
    for push in result.pushes:
        print(f"  push {push}")
    if result.timeline is not None:
        started = result.timeline["started_at"]
        print(
            f"  diagnostics: state={result.timeline['state']} outcome={result.timeline['outcome']}"
        )
        for mark in result.timeline["marks"]:
            print(f"    {mark['kind']:<10} {mark['name']:<32} {mark['at']}")
        print(f"    (started {started}, ended {result.timeline['ended_at']})")


def report_metrics(metrics: Json) -> None:
    print("\n== measurements, every call rehearsed")
    for measure in sorted(metrics["measures"], key=lambda each: each["metric"]):
        labels = ",".join(f"{k}={v}" for k, v in sorted(measure["labels"].items()))
        print(
            f"  {measure['metric']}{{{labels}}}: n={measure['count']} p50={measure['p50']:.3f} "
            f"p90={measure['p90']:.3f} max={measure['maximum']:.3f}"
        )
    failures = [
        each for each in metrics["counts"] if "fail" in each["metric"] or "error" in each["metric"]
    ]
    for count in failures:
        print(f"  {count['metric']}{count['labels']}: {count['count']}")
    print(f"  circuits: {metrics['dependencies']}")


def _seconds(value: float | None) -> str:
    return "never" if value is None else f"{value:.2f}s"


# ----------------------------------------------------------------------------------------- running


async def main_async(env_file: Path, server_url: str, only: str | None, log_level: str) -> int:
    env = read_env(env_file)
    missing = [
        name
        for name in (
            "SPEECH_PROVIDER",
            "SPEECH_ENDPOINT_URL",
            "SPEECH_VOICES",
            "SPEECH_DEFAULT_VOICE",
            "LLM_BASE_URL",
            "LLM_MODEL",
        )
        if name not in env
    ]
    if missing:
        print(f"missing from the env file: {', '.join(missing)}", file=sys.stderr)
        return 2
    pushes = Pushes()
    pushes.install()
    voice = CallerVoice(env)
    token = secrets.token_urlsafe(32)
    diagnostics = {"Authorization": f"Bearer {token}"}
    results: list[Result] = []
    async with throwaway_database(server_url) as database:
        settings = rehearsal_settings(env, database, token, log_level)
        provider = SimulatedTwilio()
        observability = build_observability(settings)
        binding = build_call_transport(
            settings,
            reported_calls=build_reported_calls(),
            observability=observability,
            http_transport=provider.rest,
        )
        # No assistant is handed in: the composition root builds the speech service and the agent
        # from settings, as a deployment's does.
        app = create_app(settings, telephony=binding, observability=observability)
        async with serving(app) as url:
            provider.attach(url)
            api = AppClient(app, url)
            try:
                account = await api.sign_in(USERS_LINE)
                # Messages allowed and nothing more, the way a user who has just set up the app
                # has it: the assistant may take a message, and anything else is the user's.
                preferences = {
                    **call_handling(escalate_at_or_above=40),
                    "authority": {"capabilities": ["take_a_message"]},
                }
                await api.configure(account, preferences)
                await api.register_device(account, "ios", "ios-device-token-rehearsal")
                await api.register_device(account, "android", "android-device-token-rehearsal")
                for scenario in SCENARIOS:
                    if only is not None and not scenario.name.startswith(only):
                        continue
                    print(f"\n-- placing {scenario.name}")
                    results.append(
                        await rehearse(
                            app, api, provider, account, voice, pushes, scenario, diagnostics
                        )
                    )
                for result in results:
                    report(result)
                metrics = await api.http.get("/diagnostics/metrics", headers=diagnostics)
                report_metrics(metrics.json())
            finally:
                await api.aclose()
                await provider.close()
    replies = [each for result in results for each in result.replies if each is not None]
    if replies:
        print(
            f"\n  reply latency across calls: median {statistics.median(replies):.2f}s, "
            f"max {max(replies):.2f}s"
        )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument(
        "--database-server",
        default="postgresql+asyncpg://letmehandle:letmehandle@127.0.0.1:5433/letmehandle",
        help="a PostgreSQL server the run may create and drop a database on",
    )
    parser.add_argument("--only", choices=("A", "B"), help="rehearse one scenario")
    parser.add_argument("--log-level", default="warning")
    arguments = parser.parse_args()
    raise SystemExit(
        asyncio.run(
            main_async(
                arguments.env_file, arguments.database_server, arguments.only, arguments.log_level
            )
        )
    )


if __name__ == "__main__":
    main()
