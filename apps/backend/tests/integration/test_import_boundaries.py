"""The rule that keeps this project replaceable must fail when it is broken."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

# The whole architecture rests on the domain layer importing nothing from outside itself
# (D-003). A contract file that is never seen to fail is a contract nobody has checked: it can
# be silently misconfigured — wrong package name, wrong option — and go on reporting success.
# So this test breaks the rule on purpose and asserts that the build notices.

BACKEND = Path(__file__).resolve().parents[2]
DOMAIN = BACKEND / "src" / "letmehandle" / "domain"
VIOLATION = DOMAIN / "_boundary_violation_fixture.py"


def run_contracts() -> subprocess.CompletedProcess[str]:
    """Run import-linter exactly as ``make verify`` does.

    The console script, not ``python -m importlinter``: the module entry point exits zero
    whatever it finds, which would make this test pass while proving nothing.
    """
    executable = shutil.which("lint-imports")
    assert executable is not None, "lint-imports is not installed; run uv sync --all-extras"
    # The executable comes from shutil.which, not from anything a caller supplies.
    return subprocess.run(  # noqa: S603
        [executable], cwd=BACKEND, capture_output=True, text=True, check=False
    )


def test_the_contracts_hold_as_they_are() -> None:
    assert run_contracts().returncode == 0


@pytest.mark.parametrize(
    ("statement", "what", "reported_as"),
    [
        ("import sqlalchemy", "a database driver", "sqlalchemy"),
        ("import fastapi", "a web framework", "fastapi"),
        ("from letmehandle.adapters import database", "an adapter", "letmehandle.adapters"),
    ],
)
def test_a_forbidden_import_in_the_domain_fails_the_build(
    statement: str, what: str, reported_as: str
) -> None:
    """Written to a real module because import-linter reads the tree, not a string."""
    VIOLATION.write_text(f'"""Temporary fixture: {what}."""\n\n{statement}\n')
    try:
        result = run_contracts()
    finally:
        VIOLATION.unlink(missing_ok=True)

    assert result.returncode != 0, (
        f"importing {what} from the domain layer was allowed. The contracts in "
        f"pyproject.toml are not protecting the boundary they claim to."
    )
    assert "Broken contracts" in result.stdout
    assert reported_as in result.stdout


def test_the_fixture_leaves_nothing_behind() -> None:
    assert not VIOLATION.exists()
