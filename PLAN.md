# Build plan

LetMeHandle is built in phases. A phase is a contract: it states what will exist when the
phase is done, and what will not. The phase files are written before the code so that the
result can be audited against them rather than against memory.

Each phase file is `docs/plan/PHASE-NN-<name>.md` and has the same shape:

- **Goal** — one sentence.
- **In scope** — the complete list of what gets built.
- **Explicitly out of scope** — what a reviewer might expect and will not find, and why.
- **Deliverables** — files and modules, named.
- **Tests required** — by kind, with what each must prove.
- **Acceptance criteria** — checkable statements, numbered, referenced by the report.
- **Verification** — the checklist every phase must pass before the next one starts.
- **Risks and open questions** — anything that could invalidate the plan.

## Status

| Phase | Name | Status |
| --- | --- | --- |
| 0 | Project foundation | not started |
| 1 | Domain model and core contracts | not started |
| 2 | Authentication and user foundation | not started |
| 3 | Onboarding and preferences | not started |
| 4 | Voice configuration | not started |
| 5 | Realtime speech foundation | not started |
| 6 | Agent and decision-making | not started |
| 7 | Call transport | not started |
| 8 | Call orchestration | not started |
| 9 | Mobile core experience | not started |
| 10 | Human escalation experience | not started |
| 11 | Call summary and history | not started |
| 12 | Security, privacy and hardening | not started |
| 13 | Observability and failure handling | not started |
| 14 | Full system end to end | not started |
| 15 | Release readiness | not started |

## The completion rule

A phase is complete only when every one of these is true. There is no partial credit and no
carrying an item into the next phase.

1. Every planned task is implemented.
2. Every acceptance criterion passes.
3. Unit tests pass.
4. Integration tests pass.
5. End-to-end tests relevant to the phase pass.
6. Static analysis passes.
7. Linting passes.
8. Formatting passes.
9. Type checking passes.
10. No known regression exists.
11. Documentation affected by the phase is updated.
12. Coverage meets the floors in `docs/architecture/decisions.md` (D-020).
13. Critical domain logic is covered meaningfully, not incidentally.
14. The application runs.
15. The primary flow is verified by hand where automation cannot reach it.

The verification report for each phase is appended to its phase file, using the template in
`docs/plan/VERIFICATION-TEMPLATE.md`.

## Decisions

Architectural decisions are recorded once in `docs/architecture/decisions.md` and cited by
id (`D-001`) from the phase files. A phase file never restates a decision; if a phase needs
a decision changed, the decision record is amended and the change is visible in history.
