"""The disclosure audit reads every added line of a diff, and only those."""

from __future__ import annotations

from tests.support.scripts import load_script

audit = load_script("disclosure_audit")

DIFF = """\
diff --git a/docs/a.md b/docs/a.md
index 1111111..2222222 100644
--- a/docs/a.md
+++ b/docs/a.md
@@ -1,0 +1,3 @@
+first
+++counter
-removed
diff --git a/scripts/disclosure_audit.py b/scripts/disclosure_audit.py
--- a/scripts/disclosure_audit.py
+++ b/scripts/disclosure_audit.py
@@ -1 +1 @@
+the gate itself
diff --git a/gone.md b/gone.md
deleted file mode 100644
--- a/gone.md
+++ /dev/null
@@ -1 +0,0 @@
-was here
"""


def test_every_added_line_is_read_with_its_path() -> None:
    assert list(audit.added_lines(DIFF)) == [("docs/a.md", "first"), ("docs/a.md", "++counter")]


def test_an_added_line_that_starts_with_plus_signs_is_scanned() -> None:
    blocked = "hack" + "athon"
    tier1, tier2 = audit.compiled_tier1(), audit.compiled_tier2()
    (_, line) = list(audit.added_lines(DIFF.replace("++counter", f"++ {blocked}")))[1]
    assert audit.scan_line(line, tier1, tier2)[0]
