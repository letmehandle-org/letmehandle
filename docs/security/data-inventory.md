# Personal data inventory

Everything this project holds about a person: where, why, in what form, for how long, and what
removes it. "Until the account goes" means until the user deletes their account with
`DELETE /v1/me`, which removes the user row, every row that cascades from it, and the sign-in
challenges sent to their number, after ending any call of theirs in progress (finding F9 in
[`review.md`](review.md)).

"Sealed" means AES-256-GCM under the transcript keys, bound to the user and the record
(D-014). "Clear" means readable by anyone with a database dump.

## Backend database

| Table | Personal fields | Form | Why | Kept | Removed by |
| --- | --- | --- | --- | --- | --- |
| `users` | phone number, display name, locale | Clear | The number is the identity (D-010); the name is what the assistant says it acts for | Until the account goes | `DELETE /v1/me` |
| `otp_challenges` | phone number a code was sent to, scrypt hash of the code, attempt count | Clear number, hashed code | Verifying a sign-in and counting codes per number | Until it leaves the per-number counting window (one hour by default) | The scheduled purge; `DELETE /v1/me` for the account's number |
| `refresh_tokens` | user, family, HMAC of the token, issue, rotation and revocation times | Hashed token | Rotation and reuse detection | Until the account goes; expired and revoked rows are not purged | Cascade from `users` |
| `user_preferences` | important contacts (number, label), topics, facts the assistant may disclose, working and quiet hours with time zone, handling rules | Clear JSON document | How calls are handled | Until the account goes | Cascade from `users` |
| `user_onboarding` | which setup steps were completed or skipped | Clear | Resuming setup | Until the account goes | Cascade from `users` |
| `user_devices` | push token and platform | Clear | Delivering escalation notifications | Until signed out on that device, rotated, reported dead, or the account goes | Sign-out, rotation, provider rejection, cascade |
| `calls` | caller's number and name; caller category, state, handling, times | Caller sealed; the rest clear | Call history | Until the user deletes the call or the account goes | `DELETE /v1/calls/{id}`, cascade |
| `call_participants` | who joined when (caller, assistant, user) | Clear | Call history | With the call | Cascade from `calls` |
| `call_transcript_entries` | what was said, by which speaker, when | Text sealed; speaker and time clear | Context for the agent, the user's own review, diagnosing failures (D-014) | The user's retention: 7 days by default, within the documented floor and ceiling | The scheduled purge; call deletion |
| `call_summaries` | outcome, intent, importance, extracted details, who the caller was taken to be | Detail sealed; outcome, intent, importance, times clear | The lasting record of a call once its transcript is gone | Until the user deletes the call or the account goes | Call deletion, cascade |
| `escalation_contexts` | caller label, what was established, what the caller needs, notification delivery | Label and both sentences sealed together; reason, status, delivery and times clear | The words of an escalation, for the app when a push was not delivered (D-016) | Until the user deletes the call or the account goes | Call deletion, cascade from `users` |
| `call_timeline_marks` | each state a call entered, each stage that failed and each dependency it went without, with when | Clear; a closed vocabulary of names, nothing from the caller, user or conversation | Diagnosing a call from its id alone (D-038) | With the call | Cascade from `calls` |
| `call_reports` | handset event and call identifiers, kind, screening decision, how it ended, when | Clear; no number since migration 0008 | Idempotent handset reporting (D-028) | Until the account goes | Cascade from `users` |

Audio is never stored (D-013).

## Backend process memory

| Where | What | Kept |
| --- | --- | --- |
| Rate limiter | Client address and user id as counter keys, with attempt times | The limit's window; at most 100 000 keys, oldest dropped first |
| Telephony transport | Live calls: caller and forwarding numbers, provider call identifiers, per-leg media tokens | The call's life; finished call and delivery identifiers are remembered, bounded, to refuse redelivery |
| Media stream | About five seconds of inbound call audio | Dropped oldest-first; discarded when the leg ends |
| Handset call feed | Reported call events awaiting the orchestrator | Bounded per user |
| In-process metrics | Counts, and the most recent thousand measurements per series, labelled only by declared dimensions | The process's life |
| Development code provider | Numbers codes were sent to, and the codes | The process's life; refused in production |

## Logs

Counts, event names, identifiers of calls and requests, exception types and failure kinds. A phone
number appears only masked, and only from the development code provider. Push tokens appear
truncated. Request paths are logged; no route takes personal data in its path or query. Every line
passes a scrubber that removes fields named like a number, a token or words, and anything shaped
like a number or a signed token in what remains (D-038); a call id that could be a number is logged
as `untraceable`. Spans, when exported, carry the same identifiers and no more.

## Mobile

| Where | What | Form | Kept |
| --- | --- | --- | --- |
| iOS Keychain / Android Keystore | Access and refresh token | Platform-encrypted, this device only, after first unlock | Until sign-out |
| Android app-private preferences | Call rules snapshot, including important contacts' numbers | Clear within the app sandbox; backup disabled | Replaced whenever preferences change |
| Android app-private preferences | Call events not yet reported, which may carry the caller's number | Clear within the app sandbox; backup disabled | Until the backend acknowledges them; bounded, oldest dropped |

## Gaps

- Expired and revoked refresh tokens, handset reports and escalation contexts have no retention
  bound of their own.
