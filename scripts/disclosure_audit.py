#!/usr/bin/env python3
"""Keeps what belongs in a working session out of a public repository."""

# This repository is public from its first commit. The conversation that produces it is not,
# and the boundary between them only runs one way: a push cannot be taken back, because by
# then it is in somebody else's clone and in a search index.
#
# What must not cross that boundary is set out in docs/architecture/decisions.md under D-021:
# credentials, cloud and provider account identifiers, resource names, personal data of any
# kind, and anything said in a working session that is not a technical requirement.
#
# A convention lasts exactly as long as the person who remembers it. This script is that
# convention made mechanical, so that it does not have to be remembered.
#
# ---------------------------------------------------------------------------
# Two tiers, because a gate that cries wolf is a gate somebody turns off
# ---------------------------------------------------------------------------
#
#   TIER 1  A credential, an account identifier, a personal datum, a real phone number, a
#           name. Zero tolerance. No baseline, no way to record an exception. If one of
#           these matches, something crossed the boundary.
#
#   TIER 2  Vocabulary that is usually innocent and occasionally the tell — "the operator",
#           "our strategy", "competitor". Ratcheted against scripts/disclosure_baseline.json:
#           what already exists is recorded, and any rise fails. Shrinking one file does not
#           pay for growing another, so the count only ever goes down.
#
# In text being written now — a commit message, a pull request body, the added side of a
# diff — there is no legacy to grandfather, so both tiers block.
#
# ---------------------------------------------------------------------------
# Why some patterns are base64
# ---------------------------------------------------------------------------
#
# Not obfuscation. A plain-text list of the names and addresses this gate exists to catch
# would publish them inside the gate, and would match itself on every run, so the audit
# could never pass. Decoded at run time, they exist only in memory.
#
#   See them:  python3 scripts/disclosure_audit.py --show-terms
#
# ---------------------------------------------------------------------------
# Where this runs
# ---------------------------------------------------------------------------
#
#   make verify            the working tree, beside pii-audit
#   .githooks/pre-commit   the staged content, before it is recorded
#   .githooks/commit-msg   the message, before it is recorded
#   .githooks/pre-push     every commit in the push, message and diff
#   .github/workflows      the pull request's whole range, plus its title and body
#
# No one layer holds alone: hooks are skipped by --no-verify and a workflow is skipped by an
# admin merge. They are layered because the ways around each do not overlap.

import argparse
import base64
import json
import os
import re
import subprocess
import sys

BASELINE = os.path.join("scripts", "disclosure_baseline.json")

# This file and its baseline are the only exempt paths, and they are exempt because they are
# the gate. Nothing else in the tree can be excluded.
SELF = ("scripts/disclosure_audit.py", BASELINE.replace(os.sep, "/"))

# Lockfiles are written by a package manager, not by a person, and they carry whatever upstream
# put in a package's metadata — including a maintainer's address in a deprecation notice. That
# is not this project disclosing anything, and blocking it would mean the project cannot have a
# lockfile.
#
# The exemption is from the address rule and from that rule alone. A credential, an account
# identifier, a phone number or a name in one of these files still fails, and gitleaks reads
# them too.
GENERATED = ("pnpm-lock.yaml", "apps/backend/uv.lock", "uv.lock")
EMAIL_RULE = "an email address"
IDENTITY_RULE = "an identifying name"

# No commit carries a co-author trailer, in any form. A trailer names a person and usually an
# address, and a forge adds one on its own when it squash-merges — so a merge made without an
# explicit message is how one arrives. Merges here are made with the message written out.
COAUTHOR_TRAILER = re.compile(r"^\s*co-authored-by:", re.IGNORECASE)
COAUTHOR_RULE = "a co-author trailer"

# Who a commit says wrote and committed it is published with it. Only a forge noreply address
# may appear there; anything else is a real address attached to every copy of the history.
#
# Commits the forge makes itself — a squash merge, a branch updated from its web page — are the
# exception, and the only one. Their author address comes from the merging account's own email
# setting, which nothing in this repository can change, so this rule governs what is pushed from
# a machine. Those merges are still refused a co-author trailer: merges here are made with the
# message written out.
NOREPLY_AUTHORSHIP = re.compile(r"(@users\.noreply\.github\.com|^noreply@github\.com)$")
FORGE_COMMITTER = "noreply@github.com"
AUTHORSHIP_RULE = "an address in who wrote or committed it"

# Commits that were already published when the two rules above were added, exempted from those
# two rules only and by exact id, so the history scan can pass without rewriting what every
# clone already holds. A commit is added only by a deliberate decision to leave a published
# commit as it is, recorded in the commit that adds it.
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

# Identity terms: names, and the names of unrelated projects whose mention would say more
# about who wrote this than about the code.
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

# Structural tier 1 patterns. These are shapes, not secrets, so they are readable: a reviewer
# needs to see what is being matched in order to trust the gate.
#
# Phone numbers are the interesting case. This product is built on phone numbers and its
# tests need them, so a blanket ban would be unworkable and would be worked around. Instead
# only numbers reserved for fiction are permitted — the North American 555-01xx range and the
# United Kingdom's Ofcom drama ranges — and every fixture must use one. A number outside them
# is either real or about to be, and both are a problem.
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
    # A cloud account identifier, a resource name, or anything else that names a specific
    # deployed thing.
    (r"\barn:aws[a-z\-]*:[a-z0-9\-]*:", "an AWS resource identifier"),
    (r"(?<![\w.+])\d{12}(?![\w.])", "a twelve-digit account identifier"),
    (r"\b[A-Z]{2}[0-9a-f]{32}\b", "a provider account or resource identifier"),
    # An email address. Addresses at reserved documentation domains are the exception,
    # because examples need one.
    # The final label must be alphabetic, which is what separates an address from the
    # userinfo of a URL pointing at an IP literal — "nothing@127.0.0.1" is a connection
    # string in a test fixture, not somebody's address. Reserved documentation domains are
    # excluded so that examples can have one.
    # Excluded, in order: reserved documentation domains, so examples can have an address;
    # .invalid, likewise; and the forge's own service addresses, which appear in the
    # Signed-off-by trailer of every automated dependency commit. Without the last one the gate
    # blocks every bot pull request, and a gate that blocks routine work is a gate somebody
    # switches off. A forge noreply address identifies an account that is already public in the
    # history anyway.
    (
        r"\b[\w.+-]+@(?!example\.(?:com|org|net)\b)(?![\w.-]*\.invalid\b)"
        r"(?![\w.-]*\bnoreply\.github\.com\b)(?!github\.com\b)"
        r"[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}\b",
        "an email address",
    ),
    # A street address, loosely. Deliberately loose: a false positive here is cheap and a
    # miss is not.
    (
        r"\b\d{1,5}\s+[A-Z][a-z]+\s+(?:Street|Road|Avenue|Lane|Drive|Marg|Nagar)\b",
        "a postal address",
    ),
    # A private key, in any of the usual wrappers. gitleaks catches these too; two gates
    # with different bypasses is the point.
    (r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY", "a private key"),
    # Zero tolerance rather than ratcheted: a word about the circumstances the work was done
    # in says nothing about the product and does not belong in a public repository. Tier 2
    # would allow it to be baselined; this cannot be.
    (r"\bhackathon\b", "a reference to the circumstances of the work"),
]

# Tier 2: ordinary words that are usually about the product and occasionally about the
# session that built it. Ratcheted rather than banned.
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
    # A permitted fictional number is removed before matching, so the phone rule can be
    # strict without making the test fixtures unwritable.
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

    # The ratchet. A rise in any file fails; a fall is recorded so it cannot rise back.
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
    """Every commit on every ref: messages, and the added side of every diff.

    The half a working-tree scan can never see. A file removed from the tree and called done
    stays in the commit that added it, in the pull request body the forge still serves, and in
    the commit subject — all of which are published the moment the repository is.

    This repository has been public from its first commit and every push is audited, so this is
    a backstop rather than the main gate. It is here because that only holds while the hooks
    and the workflow both hold, and because a repository that was ever private needs it before
    it is flipped.
    """
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
    # REMAINDER rather than "+": a new-branch range is "<sha> --not --remotes=origin", and
    # argparse would otherwise try to interpret --not as one of its own options.
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
