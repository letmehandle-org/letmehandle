# Android emulator smoke test, 2026-09-13

A run of the whole product by hand on an Android emulator, against a local backend with the mock
sign-in provider, the example voices and `TELEPHONY_PROVIDER=android_native`, so that handset
screening and call reports were exercised and nothing called a paid provider. Calls were placed with
the emulator console (`adb emu gsm call`, `accept`, `cancel`). Screens that hold call history set
`FLAG_SECURE`, so they were read from `uiautomator` dumps rather than screenshots.

What [`manual-verification.md`](manual-verification.md) asks of real phones and real carriers is not
covered here: an emulator has neither.

## Setup

| Item | Value |
| --- | --- |
| Emulator | Pixel 8 image, Android 16, `arm64-v8a` debug build |
| Backend | `test/emulator-smoke` branch, run outside Docker on a spare port, its own migrated database |
| Providers | `OTP_PROVIDER=mock`, builtin example voices, no speech service, model or push credentials |
| Numbers | `<user line>` and callers `<caller 1>` to `<caller 4>`, all in `+1 202 555 01xx` |

## Results

| # | Step | Result | Evidence |
| --- | --- | --- | --- |
| 1 | Fresh install, welcome, number, code `123456`, setup, home | pass | Setup asked four steps — call handling, hours (09:00–17:00 set), authority (two granted), when you're called — and no forwarding step. Each step answered `PATCH /v1/preferences` and `POST /v1/onboarding` with 200; "You're all set" named the default voice; home opened. |
| 2a | Relaunch after a force stop | pass | Home opened directly; `GET /v1/me`, `/v1/preferences`, `/v1/onboarding` answered 200; no number asked for. |
| 2b | Relaunch with the backend stopped | pass | The app stayed signed in and showed "Could not load your setup." with Try again; after the backend returned, Try again opened home. |
| 3a | Who gets through | pass | The two lanes and the spam line shown; stored call handling already matched them, so no apply button was offered (it appears only when stored rules differ). |
| 3b | Hours | pass | From 09:00 changed to 10:00 ("7 hrs"), then All the time ("24/7"); each change a 200 `PATCH`, stored `active_hours` null afterwards. |
| 3c | What it may do | pass | Turning on "Say if you're free" stored three capabilities. |
| 3d | What it may say | pass | An added fact appeared in the list and in stored `disclosable_facts`. |
| 3e | Personalise | pass | Both example voices listed; choosing B answered `PUT /v1/preferences/voice` 200 and stored `persona` B; manner Warm stored. |
| 3f | Privacy retention | pass | 7 days changed to 30; stored `transcript_retention_days` 30; the Settings row read "30 days". |
| 3g | Call screening | pass | The system's default caller ID and spam dialog granted the role (`cmd role get-role-holders` names the app); call activity permission granted; the screen showed both as on. |
| 4a | Unknown caller, rules allow | **fail, fixed** | The handset decided before ringing (`screened: ALLOW (DEFAULT_POSTURE)`, telecom `FILTERING_COMPLETED [Allow]`, then `RINGING`) and both reports were accepted, but no call was stored and Activity stayed empty: every write failed with `value too long for type character varying(64)`. Fixed in `fix(calls): store handset-reported calls whose ids exceed 64 chars`; re-run below. |
| 4a′ | Unknown caller, allowed, answered and ended (re-run) | pass | Reports incoming/allow, answered, ended/completed; call stored `completed`, handling `passed_through`, a human participant; Activity listed it under Straight through. |
| 4b | Caller the rules reject | pass | With `<caller 3>` set as an important contact with posture reject: `screened: REJECT (IMPORTANT_CONTACT)`, telecom `FILTERING_COMPLETED [Reject]`, the call never rang; reports incoming/reject and ended/screened_out; call stored `rejected`; Activity listed it under Turned away with "ended by your rules". |
| 4c | Reports sent after the backend was unreachable | pass, with a defect held | Placed while the backend was stopped; the reports were kept on the handset and delivered when the app came to the front, once each. The stored call carries the moment the reports arrived, not when the call happened (see held defect 1). |
| 5a | Call detail, allowed call | pass | Outcome Straight through, intent Not determined, importance Routine, "Nothing to keep"; no transcript row. `GET /v1/calls/{id}/transcript` answered `transcript_not_recorded`. |
| 5b | Call detail, rejected call | pass | Turned away, "Nothing was said, so nothing is kept." |
| 5c | Delete a call | pass | Confirmation sheet, Delete now; `DELETE /v1/calls/{id}` 204; the call left Activity and the database. |
| 6 | Delete account | pass | Confirmation sheet, Delete everything; `DELETE /v1/me` 204 then sign-out; welcome screen shown. The database held no row for the user in `users`, `user_preferences`, `user_onboarding`, `refresh_tokens`, `user_devices`, `call_reports`, `calls`, `call_participants`, `call_transcript_entries`, `call_summaries` or `escalation_contexts`. The handset's rules were forgotten too: the next call from the rejected number screened `ALLOW (NO_RULES)`. |
| 7 | Sign in again with the same number | pass, with a defect held | A new user was created and setup started again at step 1 of 4. The call screened while nobody was signed in was reported to the new account (see held defect 2). |

## Defects

Fixed on this branch:

1. **Handset-reported calls were never stored.** A reported call is stored as the account's
   identifier and the handset's joined (`scoped_call_id`), 73 characters for two UUIDs, and every
   call column held 64. The end-to-end scenarios used short identifiers, so they passed. They now use
   the handset's own UUID form, and migration `0011` widens the call identifier columns to 128.
2. **The screening screen promised silenced calls.** It said calls ring silently during quiet hours;
   quiet hours were removed (D-030) and the handset never silences a call. The sentence no longer
   says so.

Held, not fixed here, because each needs a decision rather than a local change:

1. **Reported calls are timed by arrival, not by when they happened.** `CallEvent` carries no time,
   so the orchestrator stamps the call's start, answer and end with the backend's clock. A call
   rung at 12:11:33 and missed at 12:11:37, reported at 12:12:00, is stored as starting at 12:12:00
   and lasting 0 s. A handset offline for hours would put its calls at the wrong time in history.
   The reports already carry `occurred_at`; using it means the event and the ledger taking a time.
2. **Calls screened while signed out reach the next account.** Sign-out clears the handset's ledger,
   but the screening service and the phone-state receiver keep recording while nobody is signed in,
   and the next sign-in reports those calls to whoever signed in, including a different account on
   the same phone.

## Limitations

- The emulator has no carrier, forwarding, real caller or push service; sections 1–3 of the manual
  script remain unrun.
- The emulator console presents every caller's number, so withheld and undelivered numbers, which
  the rules treat separately, could not be placed.
- Home shows the static "Not answering calls yet" state whatever the screening role and history
  say; the home ring is phase 9 work, not started.
- The first allowed call, placed before the fix, stays out of history: its reports were accepted
  and are not replayed, as designed for a report already stored.
