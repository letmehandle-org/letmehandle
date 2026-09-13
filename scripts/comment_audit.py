#!/usr/bin/env python3
# ruff: noqa: T201, S603
"""Fails when a file gains a multi-line docstring or comment block, ratcheted per file."""

from __future__ import annotations

import argparse
import ast
import io
import json
import subprocess
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

ROOT: Final = Path(__file__).resolve().parent.parent
BASELINE: Final = ROOT / "scripts" / "comment_baseline.json"

PYTHON_ROOTS: Final = ("apps/backend/src", "apps/backend/tests", "scripts")
TYPESCRIPT_ROOTS: Final = ("apps/mobile/src", "packages/api-client/src")
KOTLIN_ROOTS: Final = ("apps/mobile/android/app/src",)

GENERATED: Final = frozenset({"packages/api-client/src/schema.ts"})

PYTHON_PRAGMAS: Final = ("#!", "# type:", "# noqa", "# ruff:", "# fmt:", "# pragma:", "# mypy:")
C_LIKE_PRAGMAS: Final = ("eslint-", "@ts-", "prettier-ignore", "ktlint-", "detekt-")

DOCSTRING_OWNERS: Final = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True, slots=True)
class Violation:
    """One multi-line docstring or comment block, by the line it starts on."""

    line: int
    kind: str


def comment_runs(lines: Iterable[int]) -> list[Violation]:
    """A violation for each run of two or more consecutive full-line comments."""
    runs: list[list[int]] = []
    for line in sorted(set(lines)):
        if runs and runs[-1][-1] == line - 1:
            runs[-1].append(line)
        else:
            runs.append([line])
    return [Violation(run[0], "consecutive comment lines") for run in runs if len(run) > 1]


def python_violations(source: str) -> list[Violation]:
    """Multi-line docstrings and runs of full-line `#` comments in Python source."""
    violations = [
        Violation(first.lineno, "multi-line docstring")
        for node in ast.walk(ast.parse(source))
        if isinstance(node, DOCSTRING_OWNERS)
        and node.body
        and isinstance(first := node.body[0], ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
        and "\n" in first.value.value.strip()
    ]
    comment_lines = [
        token.start[0]
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
        and not token.line[: token.start[1]].strip()
        and not token.string.startswith(PYTHON_PRAGMAS)
    ]
    return sorted(violations + comment_runs(comment_lines), key=lambda each: each.line)


def _string_end(source: str, start: int, quote: str) -> int:
    """The index just past the string literal opened by `quote` at `start`."""
    index = start + len(quote)
    while index < len(source):
        if source[index] == "\\" and len(quote) == 1:
            index += 2
            continue
        if source.startswith(quote, index):
            return index + len(quote)
        if source[index] == "\n" and quote in ("'", '"'):
            return index
        index += 1
    return index


def c_like_violations(source: str) -> list[Violation]:
    """Multi-line `/* */` blocks and runs of full-line `//` comments in TypeScript or Kotlin."""
    violations: list[Violation] = []
    comment_lines: list[int] = []
    index = 0
    while index < len(source):
        if source.startswith('"""', index):
            index = _string_end(source, index, '"""')
        elif source[index] in "\"'`":
            index = _string_end(source, index, source[index])
        elif source.startswith("//", index):
            end = source.find("\n", index)
            end = len(source) if end == -1 else end
            line_start = source.rfind("\n", 0, index) + 1
            text = source[index + 2 : end].strip()
            if not source[line_start:index].strip() and not text.startswith(C_LIKE_PRAGMAS):
                comment_lines.append(source.count("\n", 0, index) + 1)
            index = end
        elif source.startswith("/*", index):
            end = source.find("*/", index + 2)
            end = len(source) if end == -1 else end + 2
            body = source[index:end]
            if "\n" in body and not body.lstrip("/*! \t").startswith(C_LIKE_PRAGMAS):
                line = source.count("\n", 0, index) + 1
                violations.append(Violation(line, "multi-line block comment"))
            index = end
        else:
            index += 1
    return sorted(violations + comment_runs(comment_lines), key=lambda each: each.line)


SCANNERS: Final[dict[str, Callable[[str], list[Violation]]]] = {
    ".py": python_violations,
    ".ts": c_like_violations,
    ".tsx": c_like_violations,
    ".kt": c_like_violations,
}
ROOTS_BY_SUFFIX: Final = {
    ".py": PYTHON_ROOTS,
    ".ts": TYPESCRIPT_ROOTS,
    ".tsx": TYPESCRIPT_ROOTS,
    ".kt": KOTLIN_ROOTS,
}


def audited(path: str) -> bool:
    """Whether the audit reads this repository-relative path."""
    suffix = Path(path).suffix
    roots = ROOTS_BY_SUFFIX.get(suffix, ())
    return path not in GENERATED and any(path.startswith(f"{root}/") for root in roots)


def repository_files(root: Path) -> list[str]:
    """Tracked and untracked, not ignored, files under the audited roots."""
    roots = sorted({*PYTHON_ROOTS, *TYPESCRIPT_ROOTS, *KOTLIN_ROOTS})
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", *roots],  # noqa: S607
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return sorted({path for path in listed if audited(path) and (root / path).is_file()})


def scan(root: Path, paths: Iterable[str]) -> dict[str, list[Violation]]:
    """Every audited file that has at least one violation, with its violations."""
    found: dict[str, list[Violation]] = {}
    for path in paths:
        source = (root / path).read_text(encoding="utf-8")
        violations = SCANNERS[Path(path).suffix](source)
        if violations:
            found[path] = violations
    return found


def load_baseline(path: Path) -> dict[str, int]:
    """The recorded count per file, empty when there is no baseline yet."""
    if not path.exists():
        return {}
    loaded: dict[str, int] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def write_baseline(path: Path, counts: dict[str, int]) -> None:
    """Record the counts, sorted by path, one file per line."""
    path.write_text(json.dumps(dict(sorted(counts.items())), indent=2) + "\n", encoding="utf-8")


def audit(root: Path, baseline_path: Path, *, update: bool) -> int:
    """Compare the tree with the baseline; exit status 1 when any file's count rose."""
    found = scan(root, repository_files(root))
    counts = {path: len(violations) for path, violations in found.items()}
    baseline = load_baseline(baseline_path)
    risen = {path: count for path, count in counts.items() if count > baseline.get(path, 0)}
    fallen = [path for path, before in baseline.items() if counts.get(path, 0) < before]

    for path, count in sorted(risen.items()):
        print(f"{path}: {baseline.get(path, 0)} -> {count}", file=sys.stderr)
        for violation in found[path]:
            print(f"  {path}:{violation.line}: {violation.kind}", file=sys.stderr)
    if risen:
        print(
            "\ncomments: a docstring or comment is one line; shorten the ones listed above",
            file=sys.stderr,
        )
        return 1
    if update and fallen:
        write_baseline(baseline_path, counts)
        print(f"comments: baseline lowered for {len(fallen)} file(s)")
    elif fallen:
        print(f"comments: {len(fallen)} file(s) improved; run with --update to lower the baseline")
    print(f"comments: no file rose ({sum(counts.values())} recorded in {len(counts)} files)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update", action="store_true", help="lower the baseline where counts fell"
    )
    arguments = parser.parse_args()
    return audit(ROOT, BASELINE, update=arguments.update)


if __name__ == "__main__":
    sys.exit(main())
