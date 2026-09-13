# Security model

What this project defends, against whom, with what, and what it leaves open. Stated plainly, so
that anybody deploying it can decide whether the residual risks are ones they can carry.

This is the design view. What was checked, and the defects found and fixed, is in
[`docs/security/review.md`](../security/review.md); every piece of personal data and where it
lives is in [`docs/security/data-inventory.md`](../security/data-inventory.md); what a deployment
has to do for itself is in
[`docs/development/self-hosting-security.md`](../development/self-hosting-security.md).

## What the system is

One backend process (FastAPI, PostgreSQL) and a mobile app. The backend holds accounts,
preferences and call records. Calls reach it through one of two transports:

- **Streaming.** A caller dials the user's own number; the user's carrier forwards the call to a
  number on a programmable telephony account; the provider calls the backend's webhooks and opens
  a media websocket carrying the call's audio. The audio goes to a realtime speech service, and a
  language model judges the call through tools (D-026, D-027).
- **Handset.** Android's call screening service decides on the phone from a snapshot of the user's
  rules, and the app reports what happened afterwards over an authenticated route (D-028). The
  backend never touches the audio.

Escalation is the user's phone ringing; a push notification carries context for that ring and is
never required for it (D-016).

## Assets

In rough order of how much harm their loss would do:

| Asset | Where | Why it matters |
| --- | --- | --- |
| `AUTH_SIGNING_KEY` | Deployment secret | Signs every access token and keys the hash refresh tokens are stored under. Whoever holds it can sign in as anybody. |
| `TRANSCRIPT_ENCRYPTION_KEYS` | Deployment secret | Opens every transcript, summary, escalation context and the caller on every call record. |
| Telephony auth token | Deployment secret | Signs every provider callback, so whoever holds it can impersonate the provider, and it controls the account's calls. |
| What was said on calls | `call_transcript_entries`, `call_summaries`, `escalation_contexts` | The most personal thing held: what strangers said to or about the user. Sealed. |
| Who called whom | `calls` | The caller's number and name. Sealed (D-014, amended). |
| Identity and preferences | `users`, `user_preferences` | The user's number, name, important contacts' numbers and labels, facts the assistant may disclose, hours. Clear in the database. |
| Sessions | `refresh_tokens`, the handset's Keychain or Keystore | A refresh token is a long-lived sign-in. Stored hashed on the server. |
| Model, speech and push credentials | Deployment secrets | Cost, and in the push case the ability to notify the app's users. |
| The user's phone line | The carrier and the transport | The assistant can make it ring, and speaks for the user on it. |

Audio is never stored (D-013), so there is no recording to protect.

## Actors

- **A caller.** Anybody who can dial the user's number. Unauthenticated, and they control
  everything they say. Most are benign; the design assumes some are not.
- **An internet attacker.** Can reach every route the deployment exposes, including the sign-in
  and webhook routes. Has no account and no provider credentials.
- **Another user of the same deployment.** Holds a valid account, and wants somebody else's data
  or calls.
- **Somebody holding a stolen token or handset.** Has one user's session.
- **Somebody with a copy of the database or a backup.** Has every row, and no keys.
- **A provider.** Telephony, speech, model and push services each see part of a call or a
  notification, and are trusted with it (see below).

Out of scope: an attacker with shell or memory access to the backend host, with the deployment's
environment variables, or with a rooted or jailbroken handset. Each of those is already past what
the software can defend: the first two hold the keys, the last holds the session.

## Trust boundaries

### Caller speech into the model

**Untrusted.** Everything a caller says reaches the speech service and, as transcript, the
language model. A caller can say anything, including text written to look like instructions, a
system line, a tool call or a claim to be the user.

The model is therefore treated as possibly persuaded, and authority is enforced in code, not in
the prompt:

- Each tool is an application object that validates its arguments against domain types and checks
  the user's granted capabilities itself, inside the tool (D-026). The grant is read from the
  authority the user configured, which nothing on a call can widen; nothing a caller says, and
  nothing in the transcript, is consulted. Every capability defaults to closed.
- Tools that would change the call's course — escalating, ending — only record what the model
  asked for. Once the model has finished, the application acts on it once, through the escalation
  policy in `domain/policy/`. A hang-up cannot cancel an escalation the rules require.
- The escalation service rings the user immediately at most once per call.
- Phone numbers never enter the model's context; an important contact contributes a label and a
  posture only.

The adversarial suite assumes the model is fully convinced and hands each tool what a convinced
model would send (`tests/unit/application/agent/test_caller_speech_is_not_instruction.py`).

### Telephony webhooks and the media stream

**Untrusted until proved.** The provider's routes exist only when `TELEPHONY_PROVIDER` is the
streaming transport; otherwise the paths are not mounted.

- A callback body is capped at 64 KiB, then its HMAC signature is verified over the configured
  `TELEPHONY_WEBHOOK_BASE_URL`, the path and query, and every form parameter, before any
  parameter is read. The URL comes from configuration, never from the request's `Host`.
- The account identifier in the callback must match the configured account.
- A repeated parameter the transport reads is refused, since the signature does not cover the
  order of its values.
- The media websocket's handshake is signed too, and a stream is attached to a call only when it
  presents that leg's single-use token, generated per leg from the system's random source and
  compared in constant time.
- Redeliveries are recognised by the provider's idempotency header, remembered in bounded memory.

### Handset reports

**Authenticated, not trusted for anything beyond the reporting account.** A report arrives with the
account's access token and is scoped to that account on arrival: the handset's call and event
identifiers are prefixed with the user's id, so one account cannot speak for another's call by
guessing an identifier. Reports are idempotent by event id, a late report does not revive an ended
call, and a handset may send 30 requests a minute. A handset can lie about its own calls; the harm
is limited to that account's own history.

### The mobile app and its API

**Untrusted client.** Every route but the two health probes and the four sign-in routes requires a
bearer access token, enforced by a dependency declared on the route and checked by an enumerator
test that fails on any unprotected route. Every repository read and write of personal data filters
by the authenticated user.

- Sign-in is a one-time code sent to a phone number. Codes are stored as salted scrypt hashes; a
  challenge allows five attempts, counted under a row lock; codes are limited to 5 an hour per
  number (in the database) and 20 an hour per source address. Every failure has one response
  shape, and requesting a code answers identically whether or not the number has an account.
- Access tokens are HS256 with the algorithm pinned, issuer and audience required, and a lifetime of
  60 to 3600 seconds (15 minutes by default). They cannot be revoked, which is why they are short.
- Refresh tokens are random, stored as an HMAC, rotated on every use; presenting a spent one
  revokes its whole family.
- Every signed-in user is limited to 300 requests a minute, counted after the token is proved and
  before the database is touched.
- Every JSON route refuses a body over 256 KiB before reading it. Schemas forbid unknown fields and
  bound every string.
- `DELETE /v1/me` ends the user's calls in progress and deletes the account and everything that
  cascades from it.
- On the handset, tokens live in the iOS Keychain or Android Keystore, available after first unlock
  and on this device only. Android backup is disabled.

### Model and speech providers

**Trusted with what they are sent; their output is untrusted.** The speech service receives the
call's audio and the context the assistant is given: how the user's calls are handled, what the
assistant may and may not do, important contacts' labels, topics, and facts the user marked
disclosable. The
language model receives that context and the transcript. What either sends back is treated as
proposals: speech is played to the caller, and tool calls go through the checks above. Credentials
for both are settings secrets sent only to the configured endpoint.

Which providers are used, and what they retain, is the deployment's choice. A deployment that
runs its own compatible speech and model servers keeps call content inside its own network.

### Push providers

**Trusted with the notification.** A notification carries the reason, a caller label, what the
caller needs and what was established — never a transcript, a recording or a number — and
passes through Apple's or Google's servers. Delivery failing never blocks an escalation.

### The database

**Holds sealed call content and clear account data.** Transcript text, summary detail, escalation
contexts and the caller on each call are AES-256-GCM under named keys, with the user, the call and
the record bound in as associated data, so a row copied, reordered or moved to another call or user
does not open. The keys are never stored in the database. The purge and migrations run without
them. Everything else in the inventory — numbers, preferences, push tokens — is in clear.

## What each control defends against

| Control | Defends against | Does not defend against |
| --- | --- | --- |
| Capability checks inside tools | A caller persuading the model to act beyond what the user granted | A caller persuading the model to *say* something in its context |
| One immediate ring per call | A caller using escalation to harass the user within one call | The same caller calling again (see residual risks) |
| Webhook signatures and account match | Forged callbacks that would create, end or redirect calls | Replay of a captured, validly signed request (no signed timestamp) |
| Per-leg media token | Attaching a stranger's websocket to a call's audio | Somebody holding the provider auth token |
| Body limits before parsing | Memory exhaustion by large bodies | Many small requests; volumetric floods |
| Code attempt limit and per-number limit | Guessing a sign-in code quickly | Slow guessing sustained over months (about 25 guesses an hour per number) |
| Uniform sign-in responses | Learning which numbers have accounts | Learning it from the SMS or the phone itself |
| Refresh rotation and family revocation | Silent long-term use of a copied refresh token | Use of a stolen access token until it expires |
| User scoping in every repository | One account reading or changing another's data | A stolen session for that account |
| Sealed columns and bound associated data | A database dump or backup revealing calls; rows swapped between users | A dump together with the transcript keys; clear account data |
| `SecretStr` settings and hidden validation input | Keys printed in tracebacks or configuration errors | Keys leaked from the host's environment |
| Error handler logging types, not messages | Personal data or secrets reaching logs through exceptions | A log call written later that interpolates them |
| Mock code provider refusing production | The fixed development code working on a production deployment | A deployment that is reachable but not set to production |

## Residual risks

These are known, accepted for now, and not bugs to report unless something makes them worse.

**Caller prompt injection is bounded, not prevented.** A caller can persuade the model. Code decides
what that can cause: no tool acts beyond the user's grant, and the phone rings immediately at most
once per call. What code cannot stop is the speech model saying things that are in its context:
the facts the user marked disclosable, labels of important contacts, topics, and how their calls
are handled. Treat
everything in preferences as something a determined caller may hear. Nothing limits escalations
across calls, so one caller who calls repeatedly can make the phone ring once per call (D-026).

**Streaming call ownership rests on the carrier's forwarded-from number.** A streaming call belongs
to the user whose sign-in number matches the `ForwardedFrom` the provider reports (D-033). The
backend cannot verify that value; it trusts the carrier and the provider. Anybody able to place a
call to the account's number that arrives with a user's number as `ForwardedFrom` — through a
misbehaving carrier or interconnect — would have that call handled, recorded and escalated as that
user's. A call with no `ForwardedFrom`, or one no user matches, is let go and recorded nowhere.
Whether every carrier sends the value on conditionally forwarded calls is not yet verified.

**Recovery assumes a single instance.** At startup the orchestrator ends every call storage lists
as unfinished, for every user, because a live call cannot outlive the process that held its audio
(D-029). A second backend process starting against the same database would end the first
process's live calls. Several other pieces of state also live in one process's memory: rate limit
counters (except the per-number sign-in limit), webhook redelivery memory, and each call's media
tokens. Run exactly one backend process per database.

**Handset calls have no duration bound.** A call reported by a handset stays in progress until the
handset reports its end. Nothing ends it on a timer, so a handset that reports a call starting and
never reports it ending leaves that call's run held in memory, and the call unfinished in storage,
until the account is deleted or the process restarts and recovery ends it as failed. The report
rate limit slows how quickly one handset can open such calls; nothing caps how many it holds. No
streaming call has an overall duration bound either; its ring and each wait are bounded, the call
itself is not.

**Webhook replay.** The provider signs no timestamp, so a captured, validly signed request cannot be
refused by age. Capturing one requires breaking TLS between the provider and the deployment. Within
a process, a redelivery is recognised and a finished call is answered with a hang-up; across a
restart that memory is gone.

**Sign-in is only as strong as the phone number.** Whoever receives the code for a number is that
account. SIM swap and number recycling are outside what this project can defend. There is no
production code provider yet: outside `APP_ENV=production` the development provider accepts one
published code for every number, and with `APP_ENV=production` the process refuses to start until a
real provider exists.

**Access tokens outlive sign-out.** Signing out revokes the refresh token, but a stolen access
token works until it expires, up to the configured lifetime. A token whose account has been deleted
is refused, because each request that reads or writes data loads the user.

**Account data is not sealed.** Numbers, preferences, facts the user wrote and push tokens are clear
in the database and in backups. Expired and revoked refresh tokens, handset reports and escalation
contexts are kept until the account or call is deleted.

**Push token takeover.** Registering a push token moves it from any other account that held it.
Tokens are not guessable, but there is no proof of possession.

**Mobile.** No screenshot or app-switcher protection on screens that show call content. No root or
jailbreak detection. Android keeps the call rules snapshot and unreported call events in app-private
storage, relying on the platform's file encryption.

**Providers see call content.** The speech service hears every call the assistant takes, and the
model reads what was said. Their retention is governed by the deployment's agreements with them,
not by this project.
