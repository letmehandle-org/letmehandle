#!/usr/bin/env python3
# ruff: noqa: T201, S603 - a terminal tool that runs the package managers it audits
"""Audit every locked dependency for advisories; exit 0 clean, 1 refused, 2 not run."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[1]
BACKEND: Final = ROOT / "apps" / "backend"

# Pinned, so the audit is the same audit on every machine and in CI.
PIP_AUDIT: Final = "pip-audit==2.10.1"

# Accepted advisories by identifier, with the reason recorded in docs/security/review.md.
ACCEPTED: Final = {
    "GHSA-w3rx-r6r6-pgpr": "image-size, reached only through the bundler; no patched release",
    "GHSA-5p2g-fcmc-qvqq": "image-size, reached only through the bundler; no patched release",
    "GHSA-vcc3-ghjq-m6fr": (
        "decode-uri-component, unreachable with outside input while the app declares no deep links"
    ),
}


@dataclass(frozen=True, slots=True)
class Finding:
    """One advisory against one package."""

    ecosystem: str
    package: str
    advisory: str
    aliases: frozenset[str]

    def accepted_as(self) -> str | None:
        """The accepted identifier this finding is known by, if it is accepted at all."""
        return next((name for name in {self.advisory, *self.aliases} if name in ACCEPTED), None)


class AuditUnavailableError(RuntimeError):
    """An audit could not be run or could not reach its advisory database."""


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    except FileNotFoundError as error:
        raise AuditUnavailableError(f"{command[0]} is not installed") from error


def backend_findings() -> list[Finding]:
    """What pip-audit reports against the backend's locked, hashed requirements."""
    with tempfile.TemporaryDirectory() as scratch:
        requirements = Path(scratch) / "requirements.txt"
        exported = _run(
            [
                "uv",
                "export",
                "--frozen",
                "--all-extras",
                "--no-emit-project",
                "--format",
                "requirements-txt",
                "--output-file",
                str(requirements),
            ],
            cwd=BACKEND,
        )
        if exported.returncode != 0:
            raise AuditUnavailableError(f"uv could not export the lock: {exported.stderr.strip()}")
        audited = _run(
            [
                "uvx",
                PIP_AUDIT,
                "--requirement",
                str(requirements),
                "--require-hashes",
                "--disable-pip",
                "--format",
                "json",
                "--progress-spinner",
                "off",
            ],
            cwd=BACKEND,
        )
    # pip-audit prints JSON whatever it finds; unparseable output is an audit that did not run.
    try:
        report = json.loads(audited.stdout)
    except json.JSONDecodeError as error:
        message = f"pip-audit did not report: {audited.stderr.strip()}"
        raise AuditUnavailableError(message) from error
    return [
        Finding("python", dependency["name"], vuln["id"], frozenset(vuln.get("aliases", ())))
        for dependency in report["dependencies"]
        for vuln in dependency.get("vulns", ())
    ]


def workspace_findings() -> list[Finding]:
    """What pnpm reports against the workspace's lock."""
    audited = _run(["pnpm", "audit", "--json"], cwd=ROOT)
    try:
        report = json.loads(audited.stdout)
    except json.JSONDecodeError as error:
        message = f"pnpm audit did not report: {audited.stderr.strip()}"
        raise AuditUnavailableError(message) from error
    if "advisories" not in report:
        raise AuditUnavailableError(f"pnpm audit did not report: {report.get('error', report)}")
    return [
        Finding(
            "javascript",
            advisory["module_name"],
            advisory.get("github_advisory_id") or str(advisory["id"]),
            frozenset(advisory.get("cves", ())),
        )
        for advisory in report["advisories"].values()
    ]


def main() -> int:
    try:
        findings = [*backend_findings(), *workspace_findings()]
    except AuditUnavailableError as error:
        print(f"dependency audit could not run, which is not a pass: {error}", file=sys.stderr)
        return 2

    refused = [each for each in findings if each.accepted_as() is None]
    seen = {each.accepted_as() for each in findings}
    for finding in findings:
        accepted = finding.accepted_as()
        mark = "accepted" if accepted else "REFUSED"
        reason = f"  ({ACCEPTED[accepted]})" if accepted else ""
        print(f"{mark:<9} {finding.ecosystem:<10} {finding.package}  {finding.advisory}{reason}")
    for gone in sorted(set(ACCEPTED) - seen):
        print(f"no longer reported: {gone}; remove it from the accepted list")
    if refused:
        print(f"\n{len(refused)} advisory not accepted. Upgrade, or record why it is accepted.")
        return 1
    print("dependency audit passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
