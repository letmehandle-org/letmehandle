"""The comment audit's scanners and its per-file ratchet."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "comment_audit.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("comment_audit", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


audit = _load()

MULTI_LINE_PYTHON = '''"""Module.

More.
"""


class Thing:
    """Class.

    More.
    """

    def method(self) -> None:
        """Method.
        More."""


async def run() -> None:
    """Coroutine.

    More.
    """
'''


def _kinds(violations: list[Any]) -> list[tuple[int, str]]:
    return [(each.line, each.kind) for each in violations]


def test_every_multi_line_python_docstring_is_a_violation() -> None:
    assert _kinds(audit.python_violations(MULTI_LINE_PYTHON)) == [
        (1, "multi-line docstring"),
        (8, "multi-line docstring"),
        (14, "multi-line docstring"),
        (19, "multi-line docstring"),
    ]


def test_a_one_line_python_docstring_is_not_a_violation() -> None:
    source = '"""Module."""\n\n\ndef run() -> None:\n    """Runs.\n    """\n'
    assert audit.python_violations(source) == []


def test_a_run_of_full_line_hash_comments_is_one_violation() -> None:
    source = "x = 1\n# one\n# two\n# three\ny = 2\n"
    assert _kinds(audit.python_violations(source)) == [(2, "consecutive comment lines")]


def test_hash_comments_apart_trailing_or_in_strings_are_not_violations() -> None:
    source = "# one\n\n# two\nx = 1  # trailing\ny = 2  # trailing\nz = '# a\\n# b'\n"
    assert audit.python_violations(source) == []


@pytest.mark.parametrize(
    "pragma",
    ["#!/usr/bin/env python3", "# type: ignore", "# noqa: E501", "# ruff: noqa: T201"],
)
def test_python_pragmas_do_not_count_towards_a_run(pragma: str) -> None:
    assert audit.python_violations(f"{pragma}\n# the only comment\nx = 1\n") == []


def test_a_multi_line_c_like_block_is_a_violation() -> None:
    source = "/**\n * Thing.\n */\nconst a = 1;\n/* one */\n/** one */\n"
    assert _kinds(audit.c_like_violations(source)) == [(1, "multi-line block comment")]


def test_a_run_of_full_line_slash_comments_is_one_violation() -> None:
    source = "const a = 1;\n  // one\n  // two\nconst b = 2; // trailing\n// alone\n"
    assert _kinds(audit.c_like_violations(source)) == [(2, "consecutive comment lines")]


def test_c_like_pragmas_do_not_count_towards_a_run() -> None:
    source = "// eslint-disable-next-line no-console\n// @ts-expect-error\n// only\nrun();\n"
    assert audit.c_like_violations(source) == []


def test_comment_markers_inside_strings_are_not_comments() -> None:
    source = (
        "const glob = 'src/**/*.ts';\n"
        'const url = "https://example.com";\n'
        "const text = `\n// not\n// a comment\n/* nor\nthis */`;\n"
        'val raw = """\n/* kotlin\n*/\n"""\n'
    )
    assert audit.c_like_violations(source) == []


def test_generated_files_and_files_outside_the_roots_are_not_audited() -> None:
    assert audit.audited("apps/mobile/src/App.tsx")
    assert audit.audited("scripts/comment_audit.py")
    assert audit.audited("apps/mobile/android/app/src/main/java/Main.kt")
    assert not audit.audited("packages/api-client/src/schema.ts")
    assert not audit.audited("apps/backend/alembic/versions/0001_first.py")
    assert not audit.audited("apps/mobile/src/App.py")


def _repository(tmp_path: Path, source: str, baseline: dict[str, int]) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)  # noqa: S607
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "example.py").write_text(source)
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline))
    return baseline_path


TWO_VIOLATIONS = '"""A.\n\nB.\n"""\n# one\n# two\n'
ONE_VIOLATION = '"""A."""\n# one\n# two\n'


def test_a_file_whose_count_rose_fails_and_leaves_the_baseline(tmp_path: Path) -> None:
    baseline = _repository(tmp_path, TWO_VIOLATIONS, {"scripts/example.py": 1})
    assert audit.audit(tmp_path, baseline, update=True) == 1
    assert json.loads(baseline.read_text()) == {"scripts/example.py": 1}


def test_a_new_file_with_violations_fails(tmp_path: Path) -> None:
    baseline = _repository(tmp_path, ONE_VIOLATION, {})
    assert audit.audit(tmp_path, baseline, update=False) == 1


def test_a_fall_passes_without_rewriting_the_baseline(tmp_path: Path) -> None:
    baseline = _repository(tmp_path, ONE_VIOLATION, {"scripts/example.py": 2})
    assert audit.audit(tmp_path, baseline, update=False) == 0
    assert json.loads(baseline.read_text()) == {"scripts/example.py": 2}


def test_update_lowers_the_baseline_when_a_count_fell(tmp_path: Path) -> None:
    baseline = _repository(tmp_path, '"""A."""\n', {"scripts/example.py": 2, "gone.py": 1})
    assert audit.audit(tmp_path, baseline, update=True) == 0
    assert json.loads(baseline.read_text()) == {}


def test_update_leaves_an_unchanged_baseline_alone(tmp_path: Path) -> None:
    baseline = _repository(tmp_path, TWO_VIOLATIONS, {"scripts/example.py": 2})
    before = baseline.read_text()
    assert audit.audit(tmp_path, baseline, update=True) == 0
    assert baseline.read_text() == before
