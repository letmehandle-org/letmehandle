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
