"""What every end-to-end scenario stands on: a database of its own, and push services to read.

The scenarios run the application the way a deployment runs it — its own settings, its own engine,
its own lifespan starting the orchestrator — so it cannot be pointed at a schema the way the
integration suites point their engines. It gets a database instead, created for this run, named
after the process, emptied before every scenario and dropped at the end.
"""

from __future__ import annotations

import asyncio
import os
import secrets
from typing import TYPE_CHECKING

import pytest
import structlog
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from letmehandle import bootstrap
from letmehandle.adapters.database.models import Base
from letmehandle.adapters.transport.twilio import transport as transport_module
from letmehandle.domain.ports.notification import DevicePlatform
from tests.contracts.fakes import RecordingNotificationProvider
from tests.e2e.harness import Emitted, Pushes
from tests.integration.conftest_db import UNREACHABLE

if TYPE_CHECKING:
    from collections.abc import Iterator

    from letmehandle.config.settings import Settings
    from letmehandle.domain.ports.clock import Clock
    from letmehandle.domain.ports.notification import NotificationProvider

DATABASE = f"letmehandle_e2e_{os.getpid()}_{secrets.token_hex(4)}"

# How long the transport waits for news of a dialled leg before calling it unreachable. Two seconds
# in production; a scenario whose every leg is heard of waits that long for nothing at teardown.
GRACE_SECONDS = 0.2


@pytest.fixture(scope="session")
def e2e_database(database_url: str) -> Iterator[str]:
    """A database for this run, with the schema created, dropped when the run ends."""
    server = make_url(database_url)
    try:
        asyncio.run(_create(server.render_as_string(hide_password=False)))
    except UNREACHABLE as error:
        if os.environ.get("CI"):
            raise
        pytest.skip(
            f"no usable database server at {server.host}:{server.port}: {type(error).__name__}. "
            f"Start one with `make up`, or point TEST_DATABASE_URL somewhere else."
        )
    own = server.set(database=DATABASE).render_as_string(hide_password=False)
    try:
        asyncio.run(_schema(own))
        yield own
    finally:
        asyncio.run(_drop(server.render_as_string(hide_password=False)))


@pytest.fixture
async def database(e2e_database: str) -> str:
    """The run's database, emptied, so no scenario reads what another left."""
    engine = create_async_engine(e2e_database)
    try:
        async with engine.begin() as connection:
            names = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
            await connection.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    finally:
        await engine.dispose()
    return e2e_database


@pytest.fixture
def pushes(monkeypatch: pytest.MonkeyPatch) -> Pushes:
    """Both push platforms, recording what they were asked to deliver.

    Put where the application chooses its providers from configuration, because the real ones need
    credentials and a platform's servers; everything from the dispatcher inward is the product's.
    """
    recorded = Pushes(
        ios=RecordingNotificationProvider(DevicePlatform.IOS),
        android=RecordingNotificationProvider(DevicePlatform.ANDROID),
    )

    def providers(_settings: Settings, *, clock: Clock) -> tuple[NotificationProvider, ...]:
        return (recorded.ios, recorded.android)

    monkeypatch.setattr(bootstrap, "build_notification_providers", providers)
    return recorded


# Every name structlog's printing logger writes a rendered line through.
_PRINTING_METHODS = (
    "msg",
    "log",
    "debug",
    "info",
    "warn",
    "warning",
    "fatal",
    "failure",
    "err",
    "error",
    "critical",
    "exception",
)


@pytest.fixture
def emitted(monkeypatch: pytest.MonkeyPatch, pushes: Pushes) -> Emitted:
    """What the run logs and pushes, kept so a scenario can look for anything identifying in it.

    Read where a rendered line is written rather than through structlog's processors: a logger
    is cached on first use with the processors configured at the time, so a capture installed
    later would miss every logger an earlier test had already used.
    """
    recorded = Emitted(lines=[], pushes=pushes)
    printing = structlog.PrintLogger.msg

    def record(logger: structlog.PrintLogger, message: str) -> None:
        recorded.lines.append(message)
        printing(logger, message)

    for name in _PRINTING_METHODS:
        monkeypatch.setattr(structlog.PrintLogger, name, record)
    return recorded


@pytest.fixture(autouse=True)
def _short_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transport_module, "LATE_CALLBACK_GRACE_SECONDS", GRACE_SECONDS)


async def _create(server_url: str) -> None:
    engine = create_async_engine(server_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{DATABASE}"'))
    finally:
        await engine.dispose()


async def _schema(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


async def _drop(server_url: str) -> None:
    engine = create_async_engine(server_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{DATABASE}" WITH (FORCE)'))
    finally:
        await engine.dispose()
