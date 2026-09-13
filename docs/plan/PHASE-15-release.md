# Phase 15 — Release readiness

**Goal:** someone who has never seen this project can run it, understand it, extend it, and
contribute to it.

## In scope

### Clean-room verification
The central exercise: a machine with none of this project's tooling installed, following
only `README.md`, reaching a running system. Every deviation from the written instructions is
a documentation defect and is fixed. Repeated until a run needs no deviation.

### Documentation
- `README.md` — what it does, what it does not do, what it costs to run, the quick start,
  and an honest statement of maturity.
- `docs/architecture/overview.md` with a diagram: components, ports, adapters, and the path
  of a call from ring to summary.
- `docs/architecture/call-flow.md` — the state machine and the escalation sequence.
- `docs/providers/` — one page per port: the interface, the capabilities, how to implement
  one, how to configure it, and how to test it against the contract suite. Written so that a
  contributor can add a provider without reading the core.
- `docs/architecture/call-transport.md` — the `CallTransport` abstraction in full:
  - what the port is and why it is named for the concern rather than for a vendor;
  - `AndroidNativeCallTransport`: what the platform provides, what it does not, and why no
    audio capability is claimed;
  - `TwilioCallTransport`: streaming, injection, dialling and bridging;
  - a **capability matrix** — every capability against every transport, stating plainly which
    product behaviours each combination supports;
  - how a transport is selected at bootstrap, and how to override the default;
  - the extension path for SIP, carrier and IMS transports, with the capabilities such a
    transport would declare and what would then become available.
- `docs/development/` — setup, testing, the coverage gates, the commit and branch rules, and
  how to run each phase's verification.
- `docs/development/demo.md` — the demonstration walkthrough, end to end.
- Configuration reference: every variable, its meaning, whether it is required, and its
  default. Generated from the settings definition so it cannot drift.

### Release mechanics
- `CHANGELOG.md` maintained per pull request from Phase 0, and now prepared for the first
  tag.
- Release workflow producing notes from the changelog, cutting the tag, and publishing the
  backend image.
- Versioning policy stated, including what a pre-1.0 version number promises.

### Final gates
- Coverage at or above the D-020 floors, reported.
- CI green across every workflow.
- Secret scan clean over the full history, not the working tree.
- Dependency audit clean or with documented exceptions.
- `.env.example` complete and verified against the settings definition.
- A sample configuration that runs with mock providers and no paid account, so the project
  can be tried before it is committed to.
- Issue and pull request templates in place and exercised.
- Every extension point documented.
- Licence and attribution verified for every dependency.

## Explicitly out of scope

- Marketing. App store submission. Hosted infrastructure.
- A 1.0 promise. This is a first public release, and the version number says so.

## Tests required

| Kind | Must prove |
| --- | --- |
| Clean room | A fresh machine reaches a running system following only the README, with no undocumented step. |
| Documentation | Every code block in the documentation is executed and succeeds; every internal link resolves; the configuration reference matches the settings definition. |
| Sample | The sample configuration runs end to end with mock providers and no credentials. |
| Full suite | Every test from every phase passes together. |

## Acceptance criteria

1. A clean-room installation succeeds with no deviation from the README.
2. The architecture diagram exists and matches the code.
3. Every provider extension point is documented with a worked example.
4. The capability matrix is present, accurate, and matches what the transports declare in code
   — checked by a test that compares the document against the declarations, because a matrix
   that drifts is worse than none.
4. The contributing workflow is documented and has been followed for at least one change.
5. Coverage meets the D-020 floors.
6. Every CI workflow is green.
7. No secret exists anywhere in history.
8. The dependency audit is clean or every exception is justified.
9. `.env.example` is complete and verified against the settings definition.
10. The sample configuration runs with no paid account.
11. Issue and pull request templates are in place.
12. The changelog and release notes are prepared.
13. Every documentation code block executes successfully.
14. The demonstration walkthrough has been performed and recorded.

## Risks and open questions

- **Documentation drift.** Written once, wrong within a month. Mitigated by generating what
  can be generated and executing what can be executed, in CI.
- **The clean-room machine.** A container is not a clean machine for the mobile toolchain.
  The mobile half is verified on a real machine with the toolchain removed, and that is
  stated in the report rather than glossed.
- **Expectation setting.** The README must be honest about what does not work yet. An
  overstated README is the fastest way to lose the first contributors.

## Verification

```
PHASE 15 VERIFICATION

Planned tasks:        in progress — documentation, sample, configuration reference, changelog,
                      release workflow, licence report, templates and history scan done; the tag,
                      real-call verification, CI on the branch and the dependency audit held
Acceptance criteria:  9/15 passed, 6 held (table below)
Unit tests:           passed   POSTGRES_PORT=5433 make verify: backend 3091 passed, 14 skipped
Integration tests:    passed   the same run, against PostgreSQL
E2E tests:            passed   make e2e, 23 passed, in each clean-room run
Coverage:             100%     backend (9528 statements, 1744 branches), floor 98
                      mobile Jest suites 237 passed with the 90% threshold enforced
Lint:                 passed   make lint
Format:               passed   ruff format --check, prettier
Typecheck:            passed   mypy --strict including the new scripts, tsc
Static analysis:      passed   import-linter, 4 contracts kept
Build:                passed   docker compose build, in the clean-room runs
Application runs:     yes      make sample-env && make up from a fresh clone: migrate exited 0,
                               /health ok, /health/ready ready; sign-in with the mock code, profile,
                               onboarding, preferences, voices and call history answered
Manual verification:  clean-room runs of the README and the demo, recorded below; the mobile half
                      and the real-provider script held
Docs updated:         README.md, CHANGELOG.md, CONTRIBUTING.md, docs/architecture/{overview,
                      call-flow,call-transport}.md, docs/providers/{README,agent,clock,otp,speech,
                      voice,notifications,call-transport}.md, docs/development/{setup,testing,
                      workflow,verification,demo,configuration,licences}.md, PR template
Known issues:         listed under "Held" below
```

### Clean-room runs

Each run cloned the branch into an empty directory, used a separate Compose project and the
documented port overrides (`BACKEND_PORT=8100 POSTGRES_PORT=5544`, because another stack on this
machine holds 8000), and followed only documented commands. The machine had the toolchain installed;
a machine without it was not available, and the mobile half was not run.

| Run | Deviation found | Fixed by |
| --- | --- | --- |
| 1 | `cp .env.example .env && make up`: the backend exited, `AUTH_SIGNING_KEY is required`; the compose file never passed it, and nothing migrated the database | `make sample-env`; a `migrate` service and the image carrying its migrations; the auth, OTP and transcript-key settings passed to the backend |
| 1 | A failed start left a stale network; the next `make up` failed resolving `postgres` | troubleshooting entry in setup |
| 2 | `uv run --env-file ../../.env letmehandle` stopped at the unquoted spaces in `SPEECH_VOICES` | the example value quoted |
| 2 | setup said ports set in `.env` take effect through `make`; `make` exports its own defaults, which win | setup corrected |
| 2 | outside Docker the server listens on 8000 whatever `BACKEND_PORT` says; demo responses ran together | setup and demo corrected |
| 3 | README quick start then demo ran `make sample-env` twice, and the second exited 1 | an existing `.env` is left alone with exit 0 |
| 4 | none: README quick start, demo parts 1 and 2 (sign-in, five reads, `make e2e` 23 passed, one scenario file 5 passed), `docker compose down -v` | — |

### Acceptance criteria

| # | Criterion | State | Evidence |
| --- | --- | --- | --- |
| 1 | Clean room with no deviation | passed for the backend; mobile held | run 4 above |
| 2 | Architecture diagram matches the code | passed | `overview.md`; the state diagram checked by `test_call_flow_document.py` |
| 3 | Every provider extension point has a worked example | passed | an "Adding one" section on every page in `docs/providers/` |
| 4 | Capability matrix matches the declarations | passed | `test_capability_matrix_document.py` builds both transports through bootstrap |
| 4 (second) | Contributing workflow followed for one change | held | this branch follows it; no pull request opened yet |
| 5 | Coverage meets the floors | passed | 100% backend; mobile threshold enforced |
| 6 | Every CI workflow green | held | nothing pushed; `release.yml` has never run |
| 7 | No secret in history | passed, gitleaks held locally | `disclosure_audit.py --history` clean over 298 commits on all refs; gitleaks is not installed here and runs in CI |
| 8 | Dependency audit clean | held | pending phase 13's audit targets; licences are clean (`licences.md`: 0 flagged, 3 conditional) |
| 9 | `.env.example` verified against the settings | passed | `make config-reference-check`, also in CI |
| 10 | Sample configuration runs with no paid account | passed | `make sample-env && make up`, run 4 |
| 11 | Issue and PR templates in place | passed, exercise held | PR template updated; see held repository settings |
| 12 | Changelog and release notes prepared | passed | `CHANGELOG.md` 0.1.0; `release.yml` extracts it (checked locally with the same awk) |
| 13 | Every documentation code block executes | held | shell blocks in the README, setup and demo ran in the clean room; Python worked examples are fragments and are not executed |
| 14 | Demonstration performed and recorded | passed for parts 1–2; parts 3–4 held | run 4 |

### Held

- **The tag and the first release.** A maintainer's; nothing here creates one.
- **Real calls, a real model and real devices.** `docs/testing/manual-verification.md` has not been run.
- **No production sign-in provider.** No deployment is safe to expose until one exists.
- **Dependency vulnerability audit.** Phase 13.
- **Repository settings, a maintainer's:** private vulnerability reporting is disabled, yet it is the only
  channel `SECURITY.md` and the issue chooser name; the `provider` label the provider issue form
  applies does not exist.
- **The plan numbers two acceptance criteria 4**; the table keeps both.
