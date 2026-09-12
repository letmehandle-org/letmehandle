"""Building settings for a test, without reaching for the host's environment."""

from __future__ import annotations

from pydantic import PostgresDsn

from letmehandle.config.settings import Environment, LogFormat, Settings

# A database that is syntactically valid and certainly not listening. Port 1 is reserved and
# nothing in a test environment binds it, so "unreachable" is a property of the address rather
# than of whatever happens to be running on the machine.
UNREACHABLE_DATABASE = "postgresql+asyncpg://nobody:nothing@127.0.0.1:1/absent"


def make_settings(
    *,
    app_env: Environment = Environment.TEST,
    log_level: str = "critical",
    log_format: LogFormat = LogFormat.CONSOLE,
    database_url: str | None = None,
) -> Settings:
    """Settings with every field stated explicitly.

    Every value is passed, so a test never inherits a default that later changes underneath it,
    and never picks up a variable that happens to be set on the machine running it.
    """
    return Settings(
        app_env=app_env,
        log_level=log_level,
        log_format=log_format,
        database_url=PostgresDsn(database_url) if database_url is not None else None,
    )
