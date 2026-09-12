# Verification report template

Appended to the phase file when the phase is claimed complete. Every line carries evidence:
the command that was run, or the reason automation could not reach it.

```
PHASE N VERIFICATION

Planned tasks:        N/N complete
Acceptance criteria:  N/N passed
Unit tests:           passed   (command, count)
Integration tests:    passed   (command, count)
E2E tests:            passed   (command, count) | not applicable (reason)
Coverage:             NN.N%    backend, floor 98
                      NN.N%    mobile logic, floor 90
Lint:                 passed   (command)
Format:               passed   (command)
Typecheck:            passed   (command)
Static analysis:      passed   (command, includes import boundary contracts)
Build:                passed   (command)
Application runs:     yes      (how it was started, what was observed)
Manual verification:  what was done by hand, and what it proved
Docs updated:         files
Known issues:         none | numbered list, each with an owning issue
Commits:              list, one line each
```

A report with a failing line is not a report of a complete phase. It is a report of a phase
in progress.
