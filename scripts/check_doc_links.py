#!/usr/bin/env python3
"""Fails when a link between this repository's own documents points at nothing."""

# Documentation is read by following links, and a link that resolves to nothing is where a
# newcomer stops. Files move and headings are renamed without anybody searching for what pointed
# at them, so every relative link in every tracked Markdown file is checked: the file must exist,
# and a `#fragment` into a Markdown file must name one of its headings, slugged as GitHub slugs
# them. Links to other sites are not fetched; that would make the check depend on the network.
#
#   python3 scripts/check_doc_links.py

from __future__ import annotations

import re
import subprocess
import sys
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# An inline link or image, `[text](target)`, and a reference definition, `[label]: target`.
_INLINE = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_REFERENCE = re.compile(r"^\s*\[[^\]]+\]:\s*<?(\S+?)>?\s*$", re.MULTILINE)
_FENCE = re.compile(r"^(```|~~~).*?^\1", re.MULTILINE | re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
_EXTERNAL = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)


def tracked_markdown() -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.md"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return [ROOT / name for name in listed if (ROOT / name).is_file()]


def slug(heading: str) -> str:
    """The anchor GitHub gives a heading: lower case, punctuation dropped, spaces as hyphens."""
    text = re.sub(r"[`*_]|\[([^\]]*)\]\([^)]*\)", r"\1", heading).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


@cache
def anchors(path: Path) -> frozenset[str]:
    text = _FENCE.sub("", path.read_text(encoding="utf-8"))
    seen: dict[str, int] = {}
    found = set()
    for heading in _HEADING.findall(text):
        base = slug(heading)
        count = seen.get(base, 0)
        seen[base] = count + 1
        found.add(base if count == 0 else f"{base}-{count}")
    return frozenset(found)


def broken_links(document: Path) -> list[str]:
    prose = _INLINE_CODE.sub("", _FENCE.sub("", document.read_text(encoding="utf-8")))
    targets = _INLINE.findall(prose) + _REFERENCE.findall(prose)
    problems = []
    for target in targets:
        if _EXTERNAL.match(target):
            continue
        location, _, fragment = target.partition("#")
        resolved = (document.parent / location).resolve() if location else document
        if not resolved.exists():
            problems.append(f"{target}: no such file")
        elif fragment and resolved.suffix == ".md" and fragment not in anchors(resolved):
            problems.append(f"{target}: no heading with that anchor")
    return problems


def main() -> int:
    failures = 0
    for document in tracked_markdown():
        for problem in broken_links(document):
            print(f"{document.relative_to(ROOT)}: {problem}", file=sys.stderr)
            failures += 1
    if failures:
        print(f"\n{failures} broken link(s) in the documentation", file=sys.stderr)
        return 1
    print("documentation links: every relative link resolves")
    return 0


if __name__ == "__main__":
    sys.exit(main())
