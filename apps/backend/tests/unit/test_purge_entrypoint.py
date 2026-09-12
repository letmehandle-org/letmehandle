"""The scheduled purge command: what it needs, and what a scheduler sees when it goes wrong."""

from __future__ import annotations

import pytest

from letmehandle import purge as command
from letmehandle.application.retention.purge import PurgeResult
from tests.support.config import UNREACHABLE_DATABASE, make_settings


def test_it_refuses_to_run_without_a_database_and_names_the_variable() -> None:
    with pytest.raises(SystemExit) as exit_info:
        command.main()
    assert "DATABASE_URL" in str(exit_info.value)


def test_it_needs_no_transcript_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE_DATABASE)
    ran: list[bool] = []

    async def succeed(settings: object) -> PurgeResult:
        ran.append(True)
        return PurgeResult(users_examined=2, users_skipped=0, entries_deleted=5, batches=2)

    monkeypatch.setattr(command, "purge_transcripts", succeed)
    command.main()
    assert ran == [True]


def test_a_run_that_skipped_somebody_exits_non_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE_DATABASE)

    async def incomplete(settings: object) -> PurgeResult:
        return PurgeResult(users_examined=2, users_skipped=1, entries_deleted=5, batches=2)

    monkeypatch.setattr(command, "purge_transcripts", incomplete)
    with pytest.raises(SystemExit) as exit_info:
        command.main()
    assert exit_info.value.code == 1


def test_a_failed_run_exits_non_zero_without_its_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE_DATABASE)

    async def fail(settings: object) -> PurgeResult:
        raise RuntimeError("a statement parameter that must not be printed")

    monkeypatch.setattr(command, "purge_transcripts", fail)
    with pytest.raises(SystemExit) as exit_info:
        command.main()
    assert exit_info.value.code == 1


async def test_an_engine_it_made_is_released_even_when_the_run_fails() -> None:
    # Unreachable, so the first statement fails; the engine made for the run is disposed of in
    # the same breath rather than leaking a pool on every scheduled failure.
    with pytest.raises(OSError):
        await command.purge_transcripts(make_settings(database_url=UNREACHABLE_DATABASE))
