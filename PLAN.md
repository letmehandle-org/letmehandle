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
| 0 | Project foundation | **complete** |
| 1 | Domain model and core contracts | **complete** |
| 2 | Authentication and user foundation | **complete** |
| 3 | Onboarding and preferences | **complete** |
| 4 | Voice configuration | **complete** |
| 5 | Realtime speech foundation | **complete** — live run held |
| 6 | Agent and decision-making | **complete** — model evaluation held |
| 7 | Call transport | **complete** — real-call checks held |
| 8 | Call orchestration | **complete** — real-call checks held |
| 9 | Mobile core experience | not started |
| 10 | Human escalation experience | in progress — backend done, mobile and devices open |
| 11 | Call summary and history | in progress — backend done, mobile screens open |
| 12 | Security, privacy and hardening | in progress — review done, mobile and build audits open |
| 13 | Observability and failure handling | not started |
| 14 | Full system end to end | in progress — automated scenarios pass, manual run held |
| 15 | Release readiness | in progress — docs, sample, release workflow done; tag, real calls, CI and dependency audit held |

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
