# Running a phase's verification

A phase is claimed complete with a verification report appended to its file in `docs/plan/`, in the
shape of [`VERIFICATION-TEMPLATE.md`](../plan/VERIFICATION-TEMPLATE.md). Every line carries evidence:
the command that was run and what it printed, or the reason automation could not reach it.

## The automated lines

One command produces nearly all of them:

```bash
make up
make verify
```

| Report line | Where the evidence comes from |
| --- | --- |
| Unit, integration tests | the pytest summary `make verify` prints: passed and skipped counts |
| E2E tests | `make e2e`, whose scenarios also run inside `make verify` |
| Coverage | the `TOTAL` line of `make coverage`, and Jest's summary for mobile |
| Lint, format | `make lint` |
| Typecheck, static analysis | `make typecheck`: mypy strict, TypeScript, and import-linter's contracts |
| Build | the backend image, `docker compose build`; the native apps, the Mobile build workflow |
| Disclosure | `make audit`, over the tree and the whole history |

A skipped database test is not a pass. If the summary shows skips for want of PostgreSQL, start it
and run again.

## The lines automation cannot reach

| Report line | How |
| --- | --- |
| Application runs | `make up`, then `curl localhost:8000/health` and `curl localhost:8000/health/ready`; name what was observed |
| Manual verification | the sections of [`docs/testing/manual-verification.md`](../testing/manual-verification.md) that the phase touches, run against real providers and devices, recorded as that document describes |
| Model evaluation | `scripts/agent_evaluation.py` and `scripts/summary_evaluation.py`, per-class pass rates |

A line that cannot be completed is written as **held**, with what it waits on. A report with a held
or failing line is the report of a phase in progress, and `PLAN.md` says so.

## Acceptance criteria

Each criterion in the phase file gets a row: the criterion, passed or held, and the test or the
command that proves it. A criterion proven only by reading the code is not proven.

## Keeping it honest

- Numbers are copied from the run, not rounded up.
- A run that needed a workaround names the workaround, and the workaround is fixed or recorded as a
  known issue.
- Nothing in a report carries a real phone number, host, account identifier or token. Write
  `<base url>`, `<user line>` and so on.
