# Security review

An adversarial pass over the backend and the mobile app, area by area, as `PHASE-12-security.md`
asks. Each area says what was checked, how, what was found, and where the evidence is.

A finding here is a defect that was reproduced — by a test that failed before the fix, or by a
query against stored data — never a suspicion. Suggestions that would make a sound area stronger
are listed separately at the end, so the two are not confused.

What a deployment has to do for itself is not repeated here; the data it holds is listed in
[`data-inventory.md`](data-inventory.md).

## Summary

| # | Area | Finding | Severity | State |
| --- | --- | --- | --- | --- |
| F1 | Authentication | Guesses at a sign-in code sent concurrently were counted as one, so the five-attempt limit did not hold, and one code could sign in twice | High | Fixed |
| F2 | Authentication | A refresh token replayed during its own exchange was read as unused, so both exchanges succeeded and reuse went undetected | Medium | Fixed |
| F3 | Input validation | Every JSON route except handset reports read an unbounded body into memory before validating it or checking the token | Medium | Fixed |
| F4 | Rate limits | Authenticated routes had no rate limit | Medium | Fixed |
| F5 | Personal data | Handset reports kept the caller's number in a plain column that nothing read | Medium | Fixed |
| F6 | Personal data | Sign-in challenges, each holding a phone number, were never deleted | Medium | Fixed |
| F7 | Personal data | Deleting a call left its escalation context behind | Medium | Fixed |
| F8 | Transcripts | Escalation contexts stored the caller's label and what the caller said they wanted in plain text | Medium | Fixed |
| F9 | Personal data | There was no account deletion | High | Fixed |
| F10 | Authentication | Outside `APP_ENV=production` every account accepts one published development code, and no production code provider exists yet | High (deployment) | Accepted by design, documented below |
| F11 | Mobile | No screenshot or app-switcher exclusion on screens that show call content | Low | Open |

## Authentication

**Checked.** Token lifetime and signing, refresh rotation and reuse detection, revocation, one-time
code brute force, enumeration through response shape, session fixation, and each of these under
concurrent requests rather than only one after another.

**How.** Read `application/auth/service.py`, `adapters/security/`, `api/auth.py` and the storage
behind them, then drove the sign-in routes against PostgreSQL with requests sent together.

**Result.**

- Access tokens are HS256 with the algorithm pinned, audience and issuer required, expiry checked
  against the application clock, lifetime bounded 60–3600 s. Refresh tokens are stored as an HMAC,
  rotated on use, and a reused one revokes its whole family. The revocation is committed even
  though the request is refused. Sound.
- Wrong code, unknown challenge, used code and expired code produce the same status and body;
  requesting a code answers identically for numbers with and without an account. Sound.
- Session fixation does not apply: the client never supplies a session identifier; tokens are
  minted only after a code is verified.
- **F1 (fixed).** The attempt counter was read, checked and written back with no lock. Ten wrong
  guesses sent together were stored as one attempt, and the correct code still worked afterwards;
  two correct submissions of one code both signed in. The challenge row is now locked for the
  verifying unit of work.
  Evidence: `TestConcurrentAttempts` in `tests/integration/test_auth_api.py`.
- **F2 (fixed).** The same pattern on refresh: a second session read the token as unused while the
  first exchange was in flight, so a replay arriving at the same moment as the genuine use was not
  detected. The token row is now locked for the exchanging or revoking unit of work.
  Evidence: `test_a_refresh_token_being_exchanged_is_not_read_as_unused_elsewhere`.
- **F10 (accepted by design).** The only code provider is the development mock, which accepts a
  fixed, published code for every account and refuses to start when `APP_ENV=production`. The
  default `APP_ENV` is `development`. A deployment reachable from a network and not set to
  production therefore lets anybody sign in as anybody. This is D-010's intent for development,
  and production cannot be started at all until a real provider exists, but it is the single most
  dangerous misconfiguration available and must be stated to anyone deploying.

## Authorisation and user scoping

**Checked.** Every route, and every repository method that reads or writes a person's data.

**How.** An automated enumerator walks the application's routes — built with the telephony
transport and voice preview present, so optional routes are included — and fails on any route
that neither depends on verifying an access token nor appears in a written exemption list with
its reason. It is proven to fail by adding an unprotected route. Repository methods were read one
by one for a `user_id` predicate.

**Result.** No unprotected route. The exemptions are the two health probes and the four sign-in
routes, whose credential is the code or refresh token in the body. The telephony provider's
callbacks carry no session and are proved by the provider's signature in each handler. Every
repository read and write of personal data filters by user; the two that do not are system jobs
by design (`unfinished` calls at startup, and the transcript purge's paging), and neither is
reachable from a request. Cross-user reads, overwrites and deletes are refused in existing tests
for profiles, calls, transcripts, summaries, escalation contexts and handset reports.
Evidence: `tests/integration/test_route_protection.py`.

## Agent authority and prompt injection

**Checked.** Whether anything a caller says can grant a capability, trigger a tool the user did
not permit, or reach the instructions.

**How.** Read the checked-tool base and each tool's grant, and the existing adversarial suite.

**Result.** Sound. A tool validates its arguments and checks the call's authority before acting;
the grant is read from the authority alone. The suite assumes the model is fully persuaded and
hands each tool the arguments a convinced model would send, with seven injection phrasings
(role override, fake system line, forged JSON tool call, claimed identity, closing a transcript
tag); a transcript that fails the test if read proves no tool consults it. What the model can
cause is bounded by the escalation policy: at most one immediate ring per call (D-026, accepted).
Evidence: `tests/unit/application/agent/test_caller_speech_is_not_instruction.py`,
`tests/integration/test_agent_scenarios.py`.

## Secrets

**Checked.** Tracked files and history, logs, error responses, metric labels, notification
payloads, and the settings object.

**How.** `scripts/disclosure_audit.py` over the tree and history; read every log call in the
backend; read the error handlers and the metric label check.

**Result.** No secret found in the tree or history by the disclosure audit. The history secret
scanner (gitleaks) runs in CI and was not available locally. Settings hold keys as `SecretStr`, and
a validation error does not quote the value. The unhandled-error handler logs exception types and
frames, never messages, and returns only a correlation id. Access and refresh tokens have a
redacting `repr`; the transcript cipher's `repr` names key ids only.

## Personal data

**Checked.** Every table and every in-memory store that holds something about a person, why,
for how long, and how it goes. The full inventory is in [`data-inventory.md`](data-inventory.md).

**Result.**

- **F5 (fixed).** `call_reports.caller_number` held the caller's number in clear, while the call
  record seals the same number (D-014). Nothing read it back. Migration 0008 drops the column.
  Evidence: `TestStorage` in `tests/integration/test_call_reports_api.py`, which scans every
  column of every row as text.
- **F6 (fixed).** `otp_challenges` rows, each holding the number a code was sent to — for anybody
  who typed one in, account or not — had a delete method nothing called. The scheduled purge now
  deletes challenges once they have left the per-number counting window, and keeps those still
  counted so the limit is not handed back early. Evidence: `tests/integration/test_challenge_purge.py`.
- **F7 (fixed).** Deleting a call removed its transcript and summary but not its escalation
  context, which repeats who called and what they wanted. Evidence:
  `test_what_the_user_was_told_about_the_call_goes_with_it`.
- **F9 (fixed).** No route deleted an account. `DELETE /v1/me` now ends any call of the user's in
  progress through the orchestrator, waiting for each run to go within the shutdown bound, then
  removes the sign-in challenges sent to their number and the user row, from which every other
  table cascades. The residue test reads every table the schema declares, requires each to have
  held something of the person first, and finds nothing of them afterwards while another account's
  rows remain. Evidence: `tests/integration/test_account_deletion_api.py`,
  `TestEndingOneUsersCalls` in `tests/unit/application/orchestration/test_edges.py`.
- Retention gaps recorded, not yet enforced: expired and revoked refresh tokens, escalation
  contexts, and handset reports are kept until the account is deleted. See the inventory.

## Transcripts

**Checked.** Encryption at rest by inspecting stored bytes, key handling, purge, and server-side
retention bounds.

**Result.** Transcript lines, summaries, escalation contexts and the caller on the call record are
AES-256-GCM under named keys, bound to user, call, sequence, speaker and moment as associated data. Existing tests
scan every column of every row as text and hex for the words and fragments of them, and prove
that a row copied, reordered, moved to another call or another user no longer opens. The purge
holds no key, deletes in bounded batches, and is concurrency-safe. Retention is bounded in the
request schema and the domain. Sound.
Evidence: `tests/integration/test_call_storage.py`, `tests/integration/test_transcript_purge.py`.

- **F8 (fixed).** `escalation_contexts` stored `caller_label`, `established` and `needed` as plain
  text. The last two are sentences the model wrote from what the caller said, so a database dump
  carried a readable account of each escalated call, which D-014 exists to prevent. Reproduced by
  claiming a context and reading `SELECT t::text FROM escalation_contexts t`. Migration 0009
  replaces the three columns with one ciphertext and its key id, sealed under the transcript keys
  and bound to the user, the call, the reason and the moment the escalation was raised; a context
  with none of the three stores neither. The words already stored could not be sealed by a
  migration, which holds no key, and are dropped. Without transcript keys a deployment cannot
  escalate, as it cannot carry calls, and `GET /v1/escalations/{call_id}` answers
  `503 escalations_unavailable`, as call history does.
  Evidence: `tests/integration/test_escalation_context_storage.py`,
  `test_without_transcript_keys_it_is_unavailable_as_call_history_is`.

## Logs

**Checked.** Every log call in the backend and the Android and TypeScript code on the call path.

**Result.** No transcript content, token or full number found in a log call. The development code
provider logs a masked number. Push tokens are logged truncated. Failures on the Android screening
path log exception class names only, because the platform's JSON errors quote their input. Library
loggers that print prompts, headers or frames are held at warning or above. Sound. No automated
audit that fails the build on a sensitive interpolation exists yet; see the suggestions.

## Provider credentials

**Checked.** How each provider's credential is held, sent and failed on.

**Result.** Credentials are settings secrets, sent only to the configured endpoint. Revoked
notification credentials are reported as an authorisation outcome per device, without blocking the
escalation (D-016). Least privilege per provider is not yet written down; that belongs in the
self-hosting document the phase names.

## Webhooks

**Checked.** Signature verification, rejection before parsing, replay, and redelivery.

**Result.** The telephony callbacks are capped at 64 KiB, then verified over the configured public
URL and every form parameter before any is read; the account identifier must match; a repeated
parameter the transport reads is refused. The media websocket's handshake is signed, and a stream is
attached only with a single-use per-leg token compared in constant time. Redeliveries are recognised
by the provider's idempotency header in a bounded memory. **Replay window:** the provider signs no
timestamp, so a captured, validly signed request cannot be refused by age. A replay needs the TLS
traffic itself; within a process an incoming call already finished is answered with a hang-up. This
is recorded as a residual risk.

## Rate limits

**Result.**

- Sign-in: per number (5 an hour, counted in the database) and per source address (20 an hour).
  Verification is bounded by five attempts per challenge, now enforced under concurrency (F1).
- Handset reports: 30 requests a minute per user.
- **F4 (fixed).** Other authenticated routes had none. Every signed-in request is now counted per
  user once the token is proved and before the database is touched: 300 a minute, then 429 with
  `Retry-After`. Evidence: `test_a_signed_in_user_sending_more_than_any_app_does_is_refused`.
- Webhooks are not rate limited by this application; they are signature-gated and size-capped.
- All counters except the per-number one are per process (the limiter says so); a deployment with
  several processes needs a shared limiter.

## Input validation

**Result.**

- **F3 (fixed).** A body is read before the schema or the token dependency runs, and only the
  handset report route capped it. Every JSON router now refuses a body over 256 KiB with a 413
  before reading it. A test posts a 2 MiB body to every route that accepts one, telephony included.
  Evidence: `TestBodyLimits` in `tests/integration/test_route_protection.py`.
- Schemas forbid unknown fields, bound every string, and the preferences domain bounds list sizes.
  Validation errors return the field and the reason, not the value.
- Media stream frames are parsed strictly: JSON object, known event shapes, strict base64, and the
  declared format must be 8 kHz mono μ-law; audio before the stream starts closes the socket.
  Inbound audio waits in a bounded queue that drops the oldest.

## Dependencies

**How.** `pip-audit` over the backend's locked requirements; `pnpm audit` over the workspace.
Both reached their advisory databases.

**Result.** Backend: no known vulnerabilities. Workspace: three advisories, the two named in the
phase plan.

- `image-size` (two high, no patched version): reached only through the bundler. Exploiting it
  needs a hostile image already in the repository. **Accepted** until a patched release exists.
- `decode-uri-component` (moderate, patched only in an ESM-only release that breaks the test
  runner): the plan describes it as build-time only, which is **not accurate**. It is also reached
  through the navigation library's URL parsing and ships in the app bundle. It is unreachable with
  outside input today, because the app configures no deep links and declares no URL scheme or
  link intent filter. **Accepted** on that condition: adding deep linking reopens this decision.

## Mobile

**Result.**

- Session tokens are in the Keychain / Keystore via `react-native-keychain`, accessible after
  first unlock, this device only. Sound.
- Android `allowBackup` is false. The call rules snapshot and unreported call events, which hold
  numbers, are in app-private preferences, relying on the platform's file-based encryption.
- The phone-state receiver is exported, as the platform requires, and checks the action; the
  broadcast is protected, so another app cannot forge it.
- Android's cleartext setting comes from a build placeholder rather than being fixed in the
  manifest, and was not confirmed per variant in this review; iOS forbids arbitrary loads but
  allows local networking.
- Logs on the call path carry counts and failure kinds, never numbers.
- **F11 (open).** No `FLAG_SECURE` on Android and no app-switcher cover on iOS for screens that show
  call history or escalation context.
- Root and jailbreak posture: no detection. A compromised device is outside what the app defends.

## Not checked, and why

- Instrumented Android and iOS behaviour on a device: not run in this review.
- History secret scanning with gitleaks: the scanner was not installed locally; CI runs it.
- Behaviour behind a reverse proxy (forwarded client addresses for the per-source limit): depends
  on deployment configuration not present in the repository.

## Hardening suggestions

Not defects; each would strengthen an area that is currently sound.

1. **Sign-in guessing over time.** Five codes an hour at five guesses each is 25 guesses an hour per
   number, about one chance in 40 000 an hour, which accumulates over months. A daily per-number cap
   or a lockout after several exhausted challenges would bound it.
2. **Hash on a worker thread.** scrypt runs on the event loop for each code issued and each
   guess, stalling every live call's audio in that process for its duration.
3. **Refuse the development code provider on a non-loopback bind**, or require an explicit
   opt-in, so F10 cannot happen by omission.
4. **Log audit in the build.** A test that fails on a log call passing a phone number, token,
   transcript or caller field by name.
5. **Dependency audit in `make verify`**, with the two accepted advisories allow-listed by id.
6. **Media websocket frame size.** Cap the text frame size below the server's default, since an
   authenticated stream's frames are otherwise bounded only by it.
7. **Push token takeover.** Registering a token moves it from any other account. Tokens are not
   guessable, but a proof of possession (a nonce delivered by push) would close it.
8. **Profile locale.** `PATCH /v1/me` accepts any 2–16 character locale, while the preferences
   route requires a language tag.
9. **iOS local networking.** Limit `NSAllowsLocalNetworking` to debug builds.
