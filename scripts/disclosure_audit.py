#!/usr/bin/env python3
"""Blocks credentials, personal data and session talk from tracked files, commits and messages (D-021)."""

import argparse
import base64
import json
import os
import re
import subprocess
import sys

BASELINE = os.path.join("scripts", "disclosure_baseline.json")

# The gate's own files, the only paths exempt from it.
SELF = ("scripts/disclosure_audit.py", BASELINE.replace(os.sep, "/"))

# Lockfiles, exempt from the email address rule alone because upstream metadata carries addresses.
GENERATED = ("pnpm-lock.yaml", "apps/backend/uv.lock", "uv.lock")
EMAIL_RULE = "an email address"
IDENTITY_RULE = "an identifying name"

# No commit message carries a co-author trailer, in any form.
COAUTHOR_TRAILER = re.compile(r"^\s*co-authored-by:", re.IGNORECASE)
COAUTHOR_RULE = "a co-author trailer"

# A commit's author and committer are forge noreply addresses, unless the forge itself committed it.
NOREPLY_AUTHORSHIP = re.compile(r"(@users\.noreply\.github\.com|^noreply@github\.com)$")
FORGE_COMMITTER = "noreply@github.com"
AUTHORSHIP_RULE = "an address in who wrote or committed it"

# Published commits exempt, by exact id, from the authorship and co-author rules only.
PUBLISHED_BEFORE_THE_AUTHORSHIP_RULES = frozenset(
    {
        "a489677da53ff0d88dbf7cbe720093173887d625",
        "0234d7a8eaea66909a96321ed9912e8d7a0e37bf",
        "09da9e3591e760939dc0f880fb05cc66d61720bc",
        "16c7600fd831b21de38ccc6f0a75bd479383d81a",
        "205d87d6865508be23234c106d2159c51aa30686",
        "2ebb698526c13d081d84d59198fb8d01353c1956",
        "5b21fc24a6ffb919e8929edfd12e6b5821f0be7e",
        "71b5205e791eb1bebce99aed8416b1c6a2b8077a",
        "a8d2436b647bc517ef94a5589a4af4d1f807e73b",
        "a8d90b8b67df50367b71ffb45df4c0891c07326a",
        "b6e63de1c4670b0df6d58925ca8a4932cc9cd159",
        "bfccd87faa5b0568e31fce8de4ef2385568365ad",
        "d704945b689912b7d567c6786f0643865af305db",
    }
)

# Identity terms, base64 so the gate neither publishes nor matches them; see `--show-terms`.
IDENTITY_B64 = [
    "XGJuYXZlZW5cYg==",
    "XGJuYXZlZW5iaGF0dFxi",
    "XGJiaGF0dFxi",
    "XGJvcGVubGxtc1xi",
    "XGJ1dHRyZmxvd1xi",
    "XGJzYXN0YVtcc1wtXT90cmFkZXJcYg==",
    "XGJraWNoa2ljaFxi",
    "XGJ6YXByaXNlXGI=",
]

# Phone numbers in the ranges reserved for fiction, the only ones permitted.
FICTIONAL_NUMBERS = re.compile(
    r"""\+(?:
          1[2-9][0-9]{2}55501[0-9]{2}   # +1 NPA 555-01xx, reserved for fiction in North America
        | 441632960[0-9]{3}             # Ofcom drama range, geographic
        | 447700900[0-9]{3}             # Ofcom drama range, mobile
        | 442079460[0-9]{3}             # Ofcom drama range, London
        )(?![0-9])""",
    re.VERBOSE,
)

STRUCTURAL = [
    # Tier 1: a cloud account or deployed resource identifier.
    (r"\barn:aws[a-z\-]*:[a-z0-9\-]*:", "an AWS resource identifier"),
    (r"(?<![\w.+])\d{12}(?![\w.])", "a twelve-digit account identifier"),
    (r"\b[A-Z]{2}[0-9a-f]{32}\b", "a provider account or resource identifier"),
    # An email address outside example domains, .invalid and the forge's service addresses.
    (
        r"\b[\w.+-]+@(?!example\.(?:com|org|net)\b)(?![\w.-]*\.invalid\b)"
        r"(?![\w.-]*\bnoreply\.github\.com\b)(?!github\.com\b)"
        r"[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}\b",
        "an email address",
    ),
    # A street address, matched loosely.
    (
        r"\b\d{1,5}\s+[A-Z][a-z]+\s+(?:Street|Road|Avenue|Lane|Drive|Marg|Nagar)\b",
        "a postal address",
    ),
    # A private key, in any of the usual wrappers.
    (r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY", "a private key"),
    # The circumstances the work was done in, with no baseline.
    (r"\bhackathon\b", "a reference to the circumstances of the work"),
]

# Tier 2: vocabulary ratcheted per file against the baseline in a tree scan, blocking in new text.
VOCABULARY = [
    (r"\bthe operator\b", "session vocabulary"),
    (r"\bcompetitor(?:s|'s)?\b", "positioning"),
    (r"\bgo[\s-]to[\s-]market\b", "strategy"),
    (r"\bmonetis|monetiz", "strategy"),
    (r"\bpricing\s+tier\b", "strategy"),
    (r"\binvestor(?:s)?\b", "strategy"),
    (r"\bour\s+strategy\b", "strategy"),
    (r"\bmarket\s+share\b", "strategy"),
    (r"\bas\s+(?:you|we)\s+(?:said|discussed|agreed)\b", "session vocabulary"),
]


def compiled_tier1():
    """Tier 1 patterns, with the identity terms decoded at run time."""
    out = [
        (re.compile(base64.b64decode(t).decode(), re.I), "an identifying name")
        for t in IDENTITY_B64
    ]
    out += [(re.compile(p, re.I if "PRIVATE KEY" not in p else 0), why) for p, why in STRUCTURAL]
    return out


def compiled_tier2():
    return [(re.compile(p, re.I), why) for p, why in VOCABULARY]


def scan_line(line, tier1, tier2, path=None):
    """Return (tier1 hits, tier2 hits) for one line."""
    # Permitted fictional numbers are removed before matching.
    cleaned = FICTIONAL_NUMBERS.sub("", line)
    one = [why for pattern, why in tier1 if pattern.search(cleaned)]
    two = [why for pattern, why in tier2 if pattern.search(cleaned)]
    # A phone number that survived the fictional substitution is real enough to block.
    if re.search(r"\+\d{9,15}\b", cleaned):
        one.append("a phone number outside the ranges reserved for fiction")
    if path in GENERATED:
        one = [why for why in one if why != EMAIL_RULE]
    if COAUTHOR_TRAILER.match(line):
        one.append(COAUTHOR_RULE)
    return one, two


def added_lines(diff):
    """Yield (path, text) for each added line of a unified diff, skipping the gate's own files."""
    path, in_header = "?", False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            path, in_header = "?", True
        elif in_header and line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else "?"
        elif line.startswith("@@"):
            in_header = False
        elif not in_header and line.startswith("+") and path not in SELF:
            yield path, line[1:]


def tracked_files():
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True)
    return [p for p in out.stdout.splitlines() if p and p not in SELF]


def is_text(path):
    try:
        with open(path, "rb") as handle:
            return b"\0" not in handle.read(8192)
    except OSError:
        return False


def load_baseline():
    try:
        with open(BASELINE, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return {}


def report(findings, label):
    """Print findings and return whether anything was found."""
    if not findings:
        return False
    print(f"\n\033[31mdisclosure: {label}\033[0m")
    for where, why, text in findings:
        print(f"  {where}")
        print(f"    {why}: {text.strip()[:110]}")
    return True


def audit_tree():
    tier1, tier2 = compiled_tier1(), compiled_tier2()
    baseline = load_baseline()
    blocking, counts = [], {}

    for path in tracked_files():
        if not is_text(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                lines = handle.readlines()
        except OSError:
            continue
        for number, line in enumerate(lines, 1):
            one, two = scan_line(line, tier1, tier2, path)
            for why in one:
                blocking.append((f"{path}:{number}", why, line))
            if two:
                counts[path] = counts.get(path, 0) + len(two)

    failed = report(blocking, "these must not be in a tracked file")

    # A rise in any file's tier 2 count fails.
    risen = [(p, c, baseline.get(p, 0)) for p, c in counts.items() if c > baseline.get(p, 0)]
    if risen:
        failed = True
        print("\n\033[31mdisclosure: tier 2 vocabulary increased\033[0m")
        for path, now, before in risen:
            print(f"  {path}: {before} → {now}")
        print("  Rewrite the line, or if it is genuinely product copy, update the baseline")
        print("  in the same commit and say why in the message.")

    if not failed:
        print(f"disclosure: clean ({len(counts)} files carry ratcheted vocabulary)")
    return 1 if failed else 0


def audit_text(stream, label):
    tier1, tier2 = compiled_tier1(), compiled_tier2()
    findings = []
    for number, line in enumerate(stream.read().splitlines(), 1):
        one, two = scan_line(line, tier1, tier2)
        for why in one + two:
            findings.append((f"{label}, line {number}", why, line))
    return 1 if report(findings, f"{label} carries text that must not be published") else 0


def audit_history():
    """Every commit on every ref: messages, and the added side of every diff."""
    return audit_range(["--all"])


def audit_range(args):
    """Every commit in a push: its message, and the added side of its diff."""
    tier1, tier2 = compiled_tier1(), compiled_tier2()
    revs = subprocess.run(["git", "rev-list", *args], capture_output=True, text=True)
    if revs.returncode != 0:
        print(f"disclosure: could not resolve {' '.join(args)}", file=sys.stderr)
        return 1

    findings = []
    for sha in revs.stdout.split():
        grandfathered = sha in PUBLISHED_BEFORE_THE_AUTHORSHIP_RULES
        authorship = subprocess.run(
            ["git", "log", "-1", "--format=%ae%n%ce", sha], capture_output=True, text=True
        ).stdout.split()
        made_by_the_forge = authorship[-1:] == [FORGE_COMMITTER]
        if (
            not grandfathered
            and not made_by_the_forge
            and not all(NOREPLY_AUTHORSHIP.search(e) for e in authorship)
        ):
            # The address itself is not repeated: printing it would publish it in a CI log.
            findings.append((f"{sha[:8]} authorship", AUTHORSHIP_RULE, "(address withheld)"))

        message = subprocess.run(
            ["git", "log", "-1", "--format=%B", sha], capture_output=True, text=True
        ).stdout
        for line in message.splitlines():
            one, two = scan_line(line, tier1, tier2)
            if grandfathered and COAUTHOR_TRAILER.match(line):
                one = [why for why in one if why not in (COAUTHOR_RULE, IDENTITY_RULE, EMAIL_RULE)]
            for why in one + two:
                findings.append((f"{sha[:8]} message", why, line))

        diff = subprocess.run(
            ["git", "show", "--format=", "--unified=0", "--no-color", sha],
            capture_output=True,
            text=True,
        ).stdout
        for path, line in added_lines(diff):
            one, two = scan_line(line, tier1, tier2, path)
            for why in one + two:
                findings.append((f"{sha[:8]} {path}", why, line))

    return (
        1
        if report(findings, "a commit being pushed carries text that must not be published")
        else 0
    )


def audit_staged():
    """What is about to be committed: the added side of the staged diff."""
    tier1, tier2 = compiled_tier1(), compiled_tier2()
    diff = subprocess.run(
        ["git", "diff", "--cached", "--unified=0", "--no-color"], capture_output=True, text=True
    ).stdout
    findings = []
    for path, line in added_lines(diff):
        one, two = scan_line(line, tier1, tier2, path)
        for why in one + two:
            findings.append((path, why, line))
    return 1 if report(findings, "staged changes carry text that must not be published") else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    # REMAINDER, so a range such as "<sha> --not --remotes=origin" keeps its own options.
    parser.add_argument(
        "--range", nargs=argparse.REMAINDER, help="audit the commits in this rev range"
    )
    parser.add_argument("--staged", action="store_true", help="audit the staged diff")
    parser.add_argument("--history", action="store_true", help="audit every commit on every ref")
    parser.add_argument("--text", action="store_true", help="audit stdin")
    parser.add_argument("--label", default="the text", help="what to call stdin in messages")
    parser.add_argument("--show-terms", action="store_true", help="print the decoded patterns")
    args = parser.parse_args()

    if args.show_terms:
        for term in IDENTITY_B64:
            print(base64.b64decode(term).decode())
        for pattern, why in STRUCTURAL + VOCABULARY:
            print(f"{pattern}    # {why}")
        return 0
    if args.history:
        return audit_history()
    if args.staged:
        return audit_staged()
    if args.text:
        return audit_text(sys.stdin, args.label)
    if args.range:
        return audit_range(args.range)
    return audit_tree()


if __name__ == "__main__":
    sys.exit(main())
