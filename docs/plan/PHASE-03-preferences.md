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
