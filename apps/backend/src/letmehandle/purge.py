"""The scheduled purge of expired transcripts and spent sign-in challenges, as a command.

    uv run letmehandle-purge

Run it at least daily — retention is set in whole days, so a daily run keeps every transcript
within a day of its owner's setting. It is safe to run more often, to run again after a failure,
and to have two schedulers run it at once: see `application/retention/purge.py` for why.

It also deletes the sign-in challenges nothing can use or count any more, which hold the numbers
codes were sent to.

It needs DATABASE_URL and nothing else. In particular it needs no transcript key: it deletes
without reading, so the job that runs on a timer is not a job that can decrypt anything.

It exits non-zero when a run fails or cannot purge somebody, so a scheduler's own alerting sees
it. What it logs is counts, never who or what.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from letmehandle.adapters.clock import SystemClock
from letmehandle.adapters.database.call_repositories import SqlTranscriptRetentionRepository
from letmehandle.adapters.database.engine import create_engine
from letmehandle.adapters.database.repositories import (
    SqlOTPChallengeRepository,
    SqlPreferencesRepository,
)
from letmehandle.adapters.database.session import create_session_factory, unit_of_work
from letmehandle.application.auth.service import forget_spent_challenges
from letmehandle.application.retention.purge import (
    DEFAULT_BATCH_SIZE,
    PurgeResult,
    TranscriptPurge,
)
from letmehandle.config.settings import ConfigurationError, get_settings
from letmehandle.observability.logging import configure_logging, get_logger
from letmehandle.observability.metrics import LoggingMetricsRecorder

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine

    from letmehandle.config.settings import Settings
    from letmehandle.domain.ports.clock import Clock
    from letmehandle.domain.ports.metrics import MetricsRecorder
    from letmehandle.domain.ports.repositories import (
        PreferencesRepository,
        TranscriptRetentionRepository,
    )

logger = get_logger(__name__)


@dataclass(slots=True)
class _Scope:
    retention: TranscriptRetentionRepository
    preferences: PreferencesRepository


@asynccontextmanager
async def _engine_for(settings: Settings, engine: AsyncEngine | None) -> AsyncIterator[AsyncEngine]:
    """The caller's engine as it is, or one made for this run and disposed of when it ends."""
    if engine is not None:
        yield engine
        return
    made = create_engine(settings)
    try:
        yield made
    finally:
        await made.dispose()


async def purge_transcripts(
    settings: Settings,
    *,
    engine: AsyncEngine | None = None,
    clock: Clock | None = None,
    metrics: MetricsRecorder | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> PurgeResult:
    """Run one purge against the configured database, and release it afterwards.

    `engine` is for a caller that already owns one — a test pointed at its own schema. One made
    here is disposed of here.
    """
    chosen_clock = clock or SystemClock()
    async with _engine_for(settings, engine) as active:
        factory = create_session_factory(active)

        @asynccontextmanager
        async def open_scope() -> AsyncIterator[_Scope]:
            async with unit_of_work(factory) as session:
                yield _Scope(
                    retention=SqlTranscriptRetentionRepository(session),
                    preferences=SqlPreferencesRepository(session, chosen_clock),
                )

        purge = TranscriptPurge(
            open_scope=open_scope,
            clock=chosen_clock,
            metrics=metrics or LoggingMetricsRecorder(),
            batch_size=batch_size,
        )
        return await purge.run()


async def purge_expired_challenges(
    settings: Settings, *, engine: AsyncEngine | None = None, clock: Clock | None = None
) -> int:
    """Delete the sign-in challenges nothing can use or count, returning how many went."""
    async with (
        _engine_for(settings, engine) as active,
        unit_of_work(create_session_factory(active)) as session,
    ):
        return await forget_spent_challenges(
            SqlOTPChallengeRepository(session), clock or SystemClock()
        )


async def _purge_everything(settings: Settings) -> tuple[PurgeResult, int]:
    """Purge transcripts, then spent challenges, on one engine released when both are done."""
    async with _engine_for(settings, None) as engine:
        result = await purge_transcripts(settings, engine=engine)
        return result, await purge_expired_challenges(settings, engine=engine)


def main() -> None:
    """The entry point for ``uv run letmehandle-purge``."""
    try:
        settings = get_settings()
        settings.require_database_url()
    except ConfigurationError as error:
        raise SystemExit(str(error)) from error

    configure_logging(settings)
    try:
        result, challenges_deleted = asyncio.run(_purge_everything(settings))
    except Exception as error:
        # The type only. A database error's message can carry a statement's parameters.
        logger.error("transcript_purge.failed", error_type=type(error).__name__)  # noqa: TRY400
        raise SystemExit(1) from error

    logger.info(
        "transcript_purge.finished",
        users_examined=result.users_examined,
        users_skipped=result.users_skipped,
        entries_deleted=result.entries_deleted,
        batches=result.batches,
        challenges_deleted=challenges_deleted,
    )
    if not result.is_complete:
        raise SystemExit(1)
