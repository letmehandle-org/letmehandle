#!/usr/bin/env python3
# ruff: noqa: T201, S607 - a terminal tool whose output is the point
"""List the licence of every shipped dependency, and flag any the MIT licence cannot carry."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[1]
BACKEND: Final = ROOT / "apps" / "backend"
REPORT: Final = ROOT / "docs" / "development" / "licences.md"

# SPDX identifiers an MIT project ships with no obligation beyond the notice, in lower case.
PERMISSIVE: Final = frozenset(
    name.lower()
    for name in (
        "MIT",
        "MIT-0",
        "ISC",
        "0BSD",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "Apache-2.0",
        "PSF-2.0",
        "Python-2.0",
        "CC0-1.0",
        "Unlicense",
        "BlueOak-1.0.0",
        "Zlib",
    )
)

# Allowed with an obligation that is met and written down in the report, not waved through.
CONDITIONAL: Final = {
    "mpl-2.0": "file-level copyleft: changes to its own files would be published; none are made",
    "cc-by-4.0": "attribution: a data file, credited in this report",
}

# Trove classifiers and free-text licence fields, for distributions that predate SPDX metadata.
_ALIASES: Final = {
    "mit license": "MIT",
    "mit": "MIT",
    "bsd license": "BSD-3-Clause",
    "bsd": "BSD-3-Clause",
    "apache software license": "Apache-2.0",
    "apache 2.0": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache-2.0": "Apache-2.0",
    "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
    "mpl-2.0": "MPL-2.0",
    "python software foundation license": "PSF-2.0",
    "isc license (iscl)": "ISC",
}


@dataclass(frozen=True, slots=True)
class Dependency:
    ecosystem: str
    name: str
    version: str
    licence: str

    @property
    def verdict(self) -> str:
        """`ok`, the condition it is allowed under, or why it is flagged."""
        terms = [
            term.lower()
            for term in re.split(r"\s+(?:OR|AND)\s+|[()]", self.licence)
            if term.strip()
        ]
        if not terms:
            return "flagged: no licence could be read"
        chosen_any = " OR " in self.licence and " AND " not in self.licence
        allowed = [term in PERMISSIVE for term in terms]
        if all(allowed) or (chosen_any and any(allowed)):
            return "ok"
        conditions = [CONDITIONAL[term] for term in terms if term in CONDITIONAL]
        if conditions and all(term in PERMISSIVE or term in CONDITIONAL for term in terms):
            return "; ".join(conditions)
        return "flagged: not known to be compatible with MIT"

    @property
    def flagged(self) -> bool:
        return self.verdict.startswith("flagged")


def backend_runtime() -> list[tuple[str, str, bool]]:
    """Each runtime dependency, its locked version, and whether it is installed on this platform."""
    exported = subprocess.run(
        [
            "uv",
            "export",
            "--no-dev",
            "--no-hashes",
            "--no-emit-project",
            "--format",
            "requirements-txt",
        ],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    locked = []
    for line in exported.splitlines():
        match = re.match(r"^([A-Za-z0-9._-]+)==([^\s;]+)", line)
        if match is None:
            continue
        name, version = match.groups()
        try:
            importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            locked.append((name, version, False))
        else:
            locked.append((name, version, True))
    return locked


def installed_licence(name: str) -> str:
    """The distribution's SPDX expression, or the best its older metadata says."""
    metadata = importlib.metadata.metadata(name)
    expression = metadata.get("License-Expression")
    if expression:
        return str(expression)
    classifiers = [
        _ALIASES.get(each.rsplit("::", 1)[-1].strip().lower(), each.rsplit("::", 1)[-1].strip())
        for each in metadata.get_all("Classifier") or []
        if each.startswith("License ::") and "OSI Approved ::" in each
    ]
    if classifiers:
        return " OR ".join(sorted(set(classifiers)))
    free_text = (metadata.get("License") or "").strip().splitlines()
    if free_text and len(free_text[0]) <= 40:
        return _ALIASES.get(free_text[0].lower(), free_text[0])
    return ""


def backend_dependencies() -> tuple[list[Dependency], list[str]]:
    dependencies, elsewhere = [], []
    for name, version, installed in backend_runtime():
        if installed:
            dependencies.append(Dependency("backend", name, version, installed_licence(name)))
        else:
            elsewhere.append(f"{name} {version}")
    return dependencies, elsewhere


def mobile_dependencies() -> list[Dependency]:
    listed = subprocess.run(
        ["pnpm", "licenses", "list", "--prod", "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    dependencies = []
    for licence, packages in json.loads(listed).items():
        for package in packages:
            for version in package["versions"]:
                dependencies.append(
                    Dependency("mobile", package["name"], version, licence.strip("()"))
                )
    return dependencies


def report(dependencies: list[Dependency], elsewhere: list[str]) -> str:
    flagged = [each for each in dependencies if each.flagged]
    conditional = [each for each in dependencies if not each.flagged and each.verdict != "ok"]
    lines = [
        "# Third-party licences",
        "",
        "<!-- Generated by scripts/licence_report.py. Do not edit; run it again. -->",
        "",
        "Every dependency that ships with LetMeHandle, and its licence, checked against the",
        "project's own [MIT licence](../../LICENSE). The backend's runtime dependencies are read",
        "from the lock without the development group; the mobile workspace's from",
        "`pnpm licenses list --prod`. Development and CI tools are not distributed and not listed.",
        "",
        "Regenerate after a dependency changes:",
        "",
        "```bash",
        "cd apps/backend",
        "uv run python ../../scripts/licence_report.py",
        "```",
        "",
        "## Summary",
        "",
        f"- {sum(each.ecosystem == 'backend' for each in dependencies)} backend and "
        f"{sum(each.ecosystem == 'mobile' for each in dependencies)} mobile packages.",
        f"- Flagged as incompatible or unreadable: **{len(flagged)}**.",
        f"- Allowed with a condition: {len(conditional)}, listed below.",
        "",
    ]
    if flagged:
        lines += [
            "## Flagged",
            "",
            "| Package | Version | Licence | Why |",
            "| --- | --- | --- | --- |",
        ]
        lines += [f"| {e.name} | {e.version} | {e.licence or '—'} | {e.verdict} |" for e in flagged]
        lines.append("")
    if conditional:
        lines += ["## Allowed with a condition", "", "| Package | Version | Licence | Condition |"]
        lines += ["| --- | --- | --- | --- |"]
        lines += [f"| {e.name} | {e.version} | {e.licence} | {e.verdict} |" for e in conditional]
        lines.append("")
    if elsewhere:
        lines += [
            "## Locked for other platforms",
            "",
            "In the lock only for a platform the report was not generated on, so their metadata",
            "could not be read here. None is installed in the backend image, which is Linux on",
            "CPython:",
            "",
            *(f"- {each}" for each in elsewhere),
            "",
        ]
    for ecosystem in ("backend", "mobile"):
        lines += [
            f"## {ecosystem.capitalize()}",
            "",
            "| Package | Version | Licence |",
            "| --- | --- | --- |",
        ]
        lines += [
            f"| {e.name} | {e.version} | {e.licence or '—'} |"
            for e in sorted(dependencies, key=lambda each: (each.name.lower(), each.version))
            if e.ecosystem == ecosystem
        ]
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail on a flagged licence")
    arguments = parser.parse_args()

    backend, elsewhere = backend_dependencies()
    dependencies = backend + mobile_dependencies()
    flagged = [each for each in dependencies if each.flagged]
    if not arguments.check:
        REPORT.write_text(report(dependencies, elsewhere), encoding="utf-8")
        print(f"wrote {REPORT.relative_to(ROOT)}")
    for each in flagged:
        print(
            f"licence: {each.ecosystem} {each.name} {each.version}: {each.verdict}", file=sys.stderr
        )
    return 1 if flagged else 0


if __name__ == "__main__":
    raise SystemExit(main())
