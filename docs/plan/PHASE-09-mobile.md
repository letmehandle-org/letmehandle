# Phase 9 — Mobile core experience

**Goal:** the app becomes the product's face: at a glance, whether the assistant is on and
what it has been doing.

## In scope

### Design input
Designs are supplied before this phase begins. This phase does not invent the visual
language; it implements the supplied one. Anything not covered by the designs is raised
rather than guessed.

Direction, as a constraint on implementation rather than a substitute for design: dark
first, black as the primary surface, restrained accent use, generous spacing, typography
doing the work, iPhone-first polish, and as little text as the screen can carry.

### Screens
- **Home** — assistant active or inactive, and why; calls handled; escalations; blocked or
  rejected; recent activity. The state of the assistant is unambiguous without reading.
- **Activity** — the call list, grouped and filterable by outcome.
- **Call detail** — summary, intent, outcome, whether a human joined, timings, and the
  transcript where retention still holds it (D-014).
- **Settings** — account, assistant, voice, preferences, notifications, privacy and
  retention.
- **Preferences** — the Phase 3 surfaces, brought into the finished design.
- **Voice** — the Phase 4 surface, brought into the finished design.

### Foundations
- A design-token layer: colour, spacing, type, radius, motion. No literal value in a
  component; changing a token changes the app.
- A component library covering what these screens need and nothing more.
- Loading, empty, error and offline states designed and implemented for every screen. An
  empty activity list is a designed state, not a blank screen.
- Accessibility: dynamic type, contrast checked against the tokens, screen-reader labels on
  every interactive element, reduced-motion honoured.
- All strings through i18n (D-017).

### Data
- A typed API layer over the generated client, with caching, revalidation, and optimistic
  updates where they are safe.
- Pagination on activity.
- Offline: cached content readable, mutations queued or clearly refused, never silently lost.

## Explicitly out of scope

- Push notification handling. Phase 10.
- In-app audio of live calls.
- Android-specific visual polish beyond correctness. iPhone-first by intent; Android must be
  correct and usable, and is refined later.
- A web or tablet layout.

## Tests required

| Kind | Must prove |
| --- | --- |
| Unit | Hooks, state, services and the API layer at the D-020 floor: caching, invalidation, optimistic rollback, pagination, error mapping. |
| Component | Each screen renders correctly for loading, empty, error, offline, and populated states. |
| Accessibility | Every interactive element is labelled; contrast meets the standard; dynamic type does not clip or overlap at the largest setting. |
| E2E | Sign in, view activity, open a call, change a preference, change a voice, sign out. Run on a device or simulator. |
| Visual | Screenshots of each screen in the verification report, compared against the supplied designs. |

## Acceptance criteria

1. Every listed screen is implemented against the supplied designs.
2. Home communicates assistant state, handled calls, escalations and blocked calls without
   the user reading prose.
3. Every screen has designed loading, empty, error and offline states.
4. No literal colour, spacing or type value exists outside the token layer.
5. Accessibility criteria pass, including at the largest dynamic type setting.
6. No user-facing string bypasses i18n.
7. The end-to-end flow passes on a device or simulator.
8. Coverage meets the D-020 floors.

## Risks and open questions

- **Designs arriving mid-phase.** The token layer, component library, data layer and state
  handling are design-independent and are built first, so a late design does not idle the
  phase.
- **Coverage on UI code.** D-020 excludes purely presentational components deliberately.
  Anything with a branch in it is logic and is covered; the exclusion is not a place to hide.
