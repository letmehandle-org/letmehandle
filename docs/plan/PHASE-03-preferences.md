# Phase 3 — Onboarding and preferences

**Goal:** the assistant's behaviour becomes the user's structured configuration, not the
developer's assumptions.

## In scope

### Preference domain
Everything collected here is a typed value in `UserPreferences`. Nothing collected here is
ever read as free text by the agent without passing through a validated structure.

- **Call handling** — default posture for unknown callers, for known callers, for silent or
  withheld numbers.
- **Contacts and categories** — important contacts, always-pass-through contacts, and
  category-level rules (delivery, healthcare, school, sales, unknown business).
- **Interests and topics** the user wants handled or surfaced.
- **Blocked or avoided categories.**
- **Working hours and quiet hours**, stored with the user's timezone, not as naive times.
- **Behaviour and personality** — formality, verbosity, how much the assistant may
  volunteer about the user.
- **Authority boundaries** — the capability set behind `AgentAuthority`. What the assistant
  may confirm, schedule, accept, decline, or disclose. Every capability is opt-in with a
  safe default.
- **Notification preferences** — what warrants interrupting the user.

### Backend
- Migration adding preference tables. Preferences are versioned: a row records the schema
  version it was written with, so a later change can migrate rather than guess.
- `GET /v1/preferences`, `PUT /v1/preferences`, and per-section `PATCH`.
- Validation at the API boundary that mirrors the domain rules rather than duplicating them:
  the domain type is the single source of truth and the API layer constructs it.
- Onboarding progress is server-side state, so a user who reinstalls resumes where they were.

### Agent-facing context
- A `PreferenceContext` builder that renders preferences into the normalised form the agent
  consumes in Phase 6. Deterministic, versioned, snapshot-tested, and language-aware (D-017).
- This is the only place preferences become agent input. No prompt anywhere reads a
  preference field directly.

### Mobile
- Onboarding flow: introduction, then one step per preference group, resumable and
  skippable where the default is safe.
- An editable settings surface covering every value, so nothing is set-once.
- Client-side validation for immediate feedback; the server remains the authority.
- Optimistic update with rollback on failure. A failed save is visible, never silent.

## Explicitly out of scope

- Contact-list import from the device. It is a large permission and privacy surface and the
  product works without it; revisit when there is evidence it is needed.
- Learning preferences from call behaviour.
- Any agent behaviour. Phase 3 produces configuration; Phase 6 consumes it.

## Tests required

| Kind | Must prove |
| --- | --- |
| Unit | Every validation rule, at its boundary. Working hours across a midnight wrap and across a DST transition. Timezone-aware comparison. Authority capabilities default closed. |
| Unit | `PreferenceContext` is deterministic for a given input and version; snapshot tests fail on unintended drift. |
| Integration | Round-trip through the API; partial updates do not clear unset sections; preference version is recorded; onboarding progress survives a reinstall. |
| Isolation | The Phase 2 helper, applied to every new resource. |
| Mobile | Each step validates; the flow resumes at the right step; rollback restores previous state on a failed save. |
| E2E | Complete onboarding, sign out, sign in, values intact; edit later, values change. |

## Acceptance criteria

1. A user completes onboarding from the app.
2. Values survive sign-out and sign-in.
3. Every value is editable afterwards.
4. Validation exists at both the API and the UI boundary, and the API rejects what the UI
   would have.
5. A normalised agent-facing context can be generated from any valid preference set.
6. No default is more permissive than the safest reasonable option.
7. No preference value is hard-coded anywhere in the backend or the app.
8. Working hours behave correctly across midnight and across a DST change.
9. Coverage meets the D-020 floors.

## Risks and open questions

- **Scope creep into a settings encyclopedia.** Each preference must be justified by a
  decision the agent will actually make in Phase 6. Anything that cannot be is cut.
- **Timezones.** The single largest source of quiet defects here. Stored with an explicit
  zone, tested across a DST boundary, never compared naively.

---

# Phase 3 verification

```
PHASE 3 VERIFICATION

Planned tasks:        complete
Acceptance criteria:  9/9 passed
Unit tests:           passed   backend 821 passed, 3 skipped; mobile 165 passed, 16 suites
Integration tests:    passed   against a real PostgreSQL: document round-trip, the repositories,
                               the API end to end, and two concurrent saves
E2E tests:            passed   the mobile suite drives the whole application tree against a
                               stateful fake backend: onboarding start to finish, resume after a
                               restart, skip, and every settings section edited and saved
Coverage:             100.00%  backend, floor 98
                      98.5% statements / 93.0% branches mobile logic, floor 90
Lint:                 passed   ruff check; eslint --max-warnings 0; prettier --check
Format:               passed
Typecheck:            passed   mypy --strict; tsc --noEmit for the app and the generated client
Static analysis:      passed   import-linter, 3 contracts kept
Build:                passed   backend image; iOS and Android in CI
Application runs:     yes      migration 0002 applied forwards, backwards and forwards again;
                               `alembic check` confirms the models and the schema agree
Manual verification:  none beyond the automated suites. Every claim this phase makes is about
                      stored values and the shape of a request, and each is asserted
Docs updated:         decision record (D-022, D-023), development setup, the phase plan
Known issues:         none
Commits created:      24
```

## Acceptance criteria, each with its evidence

| # | Criterion | Evidence |
| --- | --- | --- |
| 1 | A user completes onboarding from the app | The mobile suite walks every step from introduction to personality and asserts the application opens |
| 2 | Values survive sign-out and sign-in | Progress and preferences are server-side; `test_progress_survives_a_new_session` signs in twice and resumes |
| 3 | Every value is editable afterwards | A settings surface with one editor per section, each covered |
| 4 | Validation at both boundaries, and the API rejects what the UI would | Sixteen hostile bodies through the API, each a 422 naming the section; the client validates the same rules for immediate feedback |
| 5 | A normalised agent-facing context can be generated | `build_preference_context`, deterministic, versioned, snapshot-tested |
| 6 | No default is more permissive than the safest option | Asserted per section: nothing granted, nothing disclosed, no notification but the one that cannot be turned off |
| 7 | No preference value is hard-coded | `TestPreferencesMateriallyChangeTheContext` drives each field separately and insists the model's input moves with it |
| 8 | Working hours behave across midnight and across a DST change | Carried from phase 1 and extended: a wrapping window, a zone-aware comparison, and the March transition |
| 9 | Coverage meets the floors | 100% backend, 98.5%/93.0% mobile logic |

## Three agents, and what came of it

The backend interfaces were written first, then three agents worked in parallel worktrees: one
built the context builder, one built the mobile flow, and one reviewed the backend adversarially
while the other two were still running.

The review paid for itself immediately. It found six real defects, three of them serious, all
invisible to a suite that was green at the time:

- **A partial save reset the half it did not carry.** Call handling and hours are two screens
  and one `CallRules`, and the HTTP layer built a whole one from whichever half arrived — filling
  the rest from the *defaults*. Saving quiet hours silently removed a user's spam blocking, and
  saving call handling silently removed their quiet hours. Reproduced end to end. The rule now
  lives where D-023 says it does: the service composes against what is stored, and the layer
  above sends only what it has. The mobile agent hit the same bug independently and worked
  around it client-side; that workaround was removed once the real fix landed.
- **Five domain invariants escaped as 500s.** They run when the service composes the whole set,
  which is outside the function that caught the rest — so a duplicate phone number in somebody's
  address book was a server fault rather than a 422 naming the field.
- **Concurrent saves lost one of the two.** Read-compose-write with no lock: two overlapping
  requests both started from the same values and the second overwrote the first, measured at
  fourteen times in fifteen. The read in `apply` is now taken for update.
- A window with seconds in it could be written and then never read again, permanently breaking
  that user's preferences endpoint; a contact label could carry newlines into what the model
  reads; an empty stored window was treated as "no window" rather than as the corruption it is;
  and a `locale` of `!!!!!!` was accepted as the language the assistant speaks.

Two smaller things the review found were already fixed by the time it reported, because they
were the same gap seen from a different angle: `disclosable_facts` had no validation and no way
to reach it. It is now a bounded value object with its own section in the API.

## What this phase deliberately does not do

`PreferenceContext` has no consumer. It is built, tested and versioned, and nothing reads it
until the agent arrives in phase 6 — which is what this phase's plan says it is for. It is not
dead code by the repository's rule: it is a delivered component whose caller is scheduled.

Onboarding progress is client-asserted: the app says which step it has completed, and the server
records it. `is_complete` therefore means "the app said it asked", not "preferences were saved
for every step". That is the right split — the server cannot know whether a user read a screen —
but it is worth knowing before anything treats completion as proof that a preference exists.
