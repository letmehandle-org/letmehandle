# Running a deployment securely

What whoever runs a deployment of this project has to do, because the code cannot do it for
them. The threat model these steps serve is in
[`docs/architecture/security.md`](../architecture/security.md); every variable named here is
documented in [`.env.example`](../../.env.example).

The values in this document are examples. `api.example.com` stands for your public host,
`+15555550100` for a phone number, and `AKIAIOSFODNN7EXAMPLE` for any credential. Never paste a
real key into an issue, a log or a pull request.

## Before anything else: the sign-in code provider

The default sign-in code provider is the development one (`OTP_PROVIDER=mock`). It sends nothing,
and it accepts **one fixed, published code for every phone number**. Anybody who can reach a
deployment running it can sign in as any user, read their calls and change their preferences.

It refuses to start when `APP_ENV=production`. `APP_ENV` defaults to `development`, and
`OTP_PROVIDER` to `mock`. Therefore:

- **Anything reachable from a network you do not fully control must run with
  `APP_ENV=production` and `OTP_PROVIDER=twilio_sms`.** The text-message provider sends each code
  from `SMS_FROM_NUMBER` on the account `SMS_ACCOUNT_ID` and `SMS_AUTH_TOKEN` name, and the process
  refuses to start naming whichever of the three is missing. It generates no code of its own and
  fixes none: the application generates each one and stores only its hash.
- **Never expose a deployment running the mock**, whatever its `APP_ENV`. A development or test
  deployment binds to loopback or sits on a private network whose every member you would trust
  with every account on it. Not behind a public tunnel, not "only for a day", not with an obscure
  URL.
- `docker-compose.yml` is for local development. Its database password is published in the
  repository.

`APP_ENV=production` also requires `AUTH_SIGNING_KEY` at startup and removes the interactive API
documentation (`/docs`, `/openapi.json`).

## Secrets

Inject every secret from a secret store or your platform's secret mechanism as environment
variables. The application reads nothing else: no key files, no mounted paths. Do not bake secrets
into an image, and do not commit a `.env` file.

The secrets are `AUTH_SIGNING_KEY`, `TRANSCRIPT_ENCRYPTION_KEYS`, `DATABASE_URL` (it carries a
password), `TELEPHONY_AUTH_TOKEN`, `SMS_AUTH_TOKEN`, `SPEECH_API_KEY`, `LLM_API_KEY`, any
`LLM_HEADERS` values, `APNS_PRIVATE_KEY` and `FCM_SERVICE_ACCOUNT_JSON`. Configuration errors name
the variable and never print its value, so an error message is safe to share; an environment dump
is not.

## The signing key

`AUTH_SIGNING_KEY` signs every access token and keys the hash under which refresh tokens are
stored. It must be at least 32 characters. Generate a fresh one for each deployment and never
reuse one between deployments or environments:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

**Rotating it** is a restart with the new value. There is no overlap period: every access token
stops verifying and no refresh token can be found, so **every user is signed out** and signs in
again. That is the intended response to a leak. Rotate when:

- the key may have been exposed — a leaked environment, a former administrator, a copied host;
- at whatever interval your own policy sets, accepting the sign-out.

## Transcript keys

`TRANSCRIPT_ENCRYPTION_KEYS` seals transcript lines, summaries, escalation contexts and the caller
on every call record with AES-256-GCM. Without it the process still serves sign-in and
preferences, but call history and escalations answer `503`, and a deployment with a call transport
configured refuses to start, because every call is recorded sealed.

Generate a key — 32 random bytes, base64 — and give it an id of 1–16 lower-case letters, digits,
`-` or `_`:

```bash
python -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

```bash
TRANSCRIPT_ENCRYPTION_KEYS=k2026a:<the generated key>
```

Keep the keys somewhere other than the database and its backups. A backup together with the keys
is every call; a backup alone is not.

**Losing every key** makes everything sealed unreadable, permanently. There is no recovery path,
by design. Back the keys up separately, with the same care as the signing key.

### Rotating a transcript key

Keys are listed newest first, comma-separated. New writes use the first; each stored row names the
key that sealed it and opens under that one.

1. Generate a new key with a new id.
2. Put it **first**, keeping every older key after it, and restart:

   ```bash
   TRANSCRIPT_ENCRYPTION_KEYS=k2026b:<new key>,k2026a:<old key>
   ```

3. Find which keys stored rows still name. All four sealed tables:

   ```sql
   SELECT key_id, count(*) FROM calls GROUP BY key_id;
   SELECT key_id, count(*) FROM call_transcript_entries GROUP BY key_id;
   SELECT key_id, count(*) FROM call_summaries GROUP BY key_id;
   SELECT key_id, count(*) FROM escalation_contexts WHERE key_id IS NOT NULL GROUP BY key_id;
   ```

4. Remove an old key only when none of those queries returns its id. Removing a key still in use
   makes those rows fail to open, with an error naming the key id.

Transcripts age out within their owner's retention, at most 90 days. Call records, summaries and
escalation contexts do not age out: a key that sealed one stays until that call or account is
deleted. There is no command that re-seals existing rows under a new key. If a transcript key
leaks, rotating stops new data being sealed under it, but rows already sealed under it stay
readable to whoever holds both that key and a copy of the database.

## Network exposure and TLS

The backend listens for plain HTTP on port 8000 on every interface of its container. It does not
terminate TLS.

- Put a reverse proxy or load balancer in front that terminates TLS, and expose only that. Port
  8000 is never reachable from outside.
- The proxy must pass WebSocket upgrades on `/telephony/media`, and must forward the path, the query
  string and the body unchanged: provider signatures cover all three.
- Keep `/health` and `/health/ready` for your own orchestration. They disclose nothing beyond the
  version and whether the database answers, but nothing outside needs them.

### The webhook base URL

With `TELEPHONY_PROVIDER=twilio`, `TELEPHONY_WEBHOOK_BASE_URL` is the public URL the provider calls,
for example `https://api.example.com`. Callback signatures are verified against this value, not
against the `Host` a request arrives with, so it must be exactly what the provider's console is
configured to call — scheme, host, port if any, and any path prefix the proxy adds — with no query
and no fragment.

Use `https`. The setting accepts `http` so a local tunnel can be used in development, but over
plain HTTP the callbacks, the media stream and its tokens travel in clear.

### Client addresses behind a proxy

Sign-in codes are limited per source address, read from the connection's peer. The server trusts
`X-Forwarded-For` only from the addresses in the `FORWARDED_ALLOW_IPS` environment variable, which
defaults to `127.0.0.1`. Behind a proxy on another address, every request appears to come from the
proxy, and all sign-ins share one limit of 20 an hour.

- Set `FORWARDED_ALLOW_IPS` to your proxy's address, and only that. Never `*` on a backend that is
  reachable other than through the proxy: a header anybody can set would choose the key the limit
  counts against.
- Configure the proxy to overwrite `X-Forwarded-For` with the address it saw, not to append to one
  the client sent.

## Rate limiting

The application limits sign-in (5 codes an hour per number, 20 an hour per source address, five
guesses a challenge), handset reports (30 requests a minute per user) and every signed-in request
(300 a minute per user). Every counter except the per-number one is held in the process's memory.
Webhooks are not rate limited; they are size-capped and signature-checked.

Add limits at the proxy as well:

- a per-address limit on `/v1/auth/`, below what a person signing in needs;
- connection and request-size limits across the board;
- no per-address limit on `/telephony/` tight enough to refuse the provider, whose requests come
  from a shared set of addresses.

## One process

Run **exactly one** backend process against a database. At startup the process ends every call
storage lists as unfinished, including calls another live process is carrying. Rate limit counters,
redelivery memory and media stream tokens are also per process, and a provider's callback has to
reach the process holding that call. Scaling out is not supported; restart rather than overlap
during a deploy, and expect calls in progress at a restart to end as failed.

## The database

- A dedicated database and role for the application, not a superuser. Nothing else connects with
  that role.
- Reachable only from the backend's network, never from the internet. Use TLS to the database when
  it is not on the same host.
- Migrations (`uv run alembic upgrade head`) run before the new version starts. They need no
  transcript keys.

### Backups

Encryption at rest covers call content, not everything. A backup contains, sealed, what was said
on calls and who called; and, in clear, users' phone numbers and names, preferences — including
important contacts' numbers and the facts a user wrote — push tokens, handset call reports and
sign-in challenges. [`data-inventory.md`](../security/data-inventory.md) lists every field.

- Encrypt backups, and restrict who can read or restore them.
- Store them apart from the transcript keys and the signing key.
- Keep them only as long as you need to. A backup outlives deletion: a call or account a user
  deleted is still in every backup taken before, and restoring one brings it back.

## Retention

How long transcripts are kept is each user's own setting: 7 days by default, between 1 and 90. The
deployment's job is to run the purge:

```bash
uv run letmehandle-purge
```

- Schedule it **at least daily**. It is safe to run more often, to re-run after a failure, and to
  overlap with itself.
- It needs `DATABASE_URL` and nothing else. Give it no transcript keys: it deletes without reading.
- It exits non-zero when a run fails or has to skip a user; alert on that. It logs counts only.
- It also deletes sign-in challenges once they leave the per-number counting window.

What it does not remove: summaries, call records and escalation contexts, which stay until the user
deletes the call or the account; expired and revoked refresh tokens; handset reports. A user
deletes everything with `DELETE /v1/me`.

## Provider credentials

Give each provider credential the least it needs, and a separate one per deployment and
environment, so one can be revoked without touching another.

- **Telephony.** The transport authenticates with the account identifier and its auth token, and
  that token also signs every callback. Use an account or subaccount that serves only this
  deployment, with only the numbers it needs. Anybody holding the token can forge callbacks and
  control calls: rotate it in the provider's console and restart if it may have leaked.
- **Text messages.** The sign-in provider needs only to send messages. Give it an account or
  subaccount that serves this deployment, and set a spending limit and the destination countries
  your users sign in from at the provider: sign-in is unauthenticated, and what limits how many
  texts a stranger can make it send is the application's per-number and per-address limits
  together with yours at the proxy. A refused number answers `422`; a provider that is throttling
  or down answers `503`, and that attempt does not count against the number.
- **Speech and model.** Keys limited to the models this deployment uses, with a spending limit set
  at the provider. On an ElevenLabs agent, allow only the overrides the adapter needs (system
  prompt, first message, language and voice). Consider what each provider retains of the audio and
  text it receives; a self-hosted compatible server keeps call content in your network.
- **APNs.** A key with push notifications only, and `APNS_ENVIRONMENT` matching the builds you ship.
  A mismatch makes Apple reject tokens as invalid, and invalid tokens are removed.
- **FCM.** A service account permitted to send messages in that one project and nothing else.

## Logs

- Set `LOG_FORMAT=json` and `LOG_LEVEL=info`. `debug` is for a development machine.
- Application logs carry event names, request method, path and status, correlation ids, call and
  request identifiers, counts and exception types. They are written not to carry transcripts,
  tokens or full phone numbers; a masked number appears only from the development code provider.
- Correlation ids come from the `x-correlation-id` request header when present. Treat them as
  labels, not as proof of anything.
- Your proxy's logs are a separate matter: do not log request bodies or `Authorization` headers
  there. Client addresses in them are personal data.
- Restrict who can read logs, and delete them on a schedule you have decided.

## The mobile app

- Release builds point `API_BASE_URL` at your `https` host. The app's configuration accepts `http`
  for local development.
- Tokens are kept in the Keychain or Keystore on the device; nothing else about sessions needs
  configuring.

## Dependencies and images

- Watch this repository's releases and security advisories, and update promptly.
- Rebuild the backend image regularly, not only on a code change: the base image receives security
  fixes of its own. The image runs as an unprivileged user; keep it that way.
- Audit the locked dependencies before a deploy, for example with `pip-audit` over requirements
  exported from `apps/backend/uv.lock`, and `pnpm audit` for the mobile workspace.

## Checklist

- [ ] `APP_ENV=production` on anything reachable from a network you do not fully control
- [ ] `OTP_PROVIDER=twilio_sms` with its `SMS_` account; never the mock where it can be reached
- [ ] `AUTH_SIGNING_KEY` generated for this deployment, stored in a secret store
- [ ] `TRANSCRIPT_ENCRYPTION_KEYS` generated, backed up apart from the database
- [ ] TLS terminated at a proxy; port 8000 not exposed
- [ ] `TELEPHONY_WEBHOOK_BASE_URL` is the exact `https` URL the provider calls
- [ ] `FORWARDED_ALLOW_IPS` set to the proxy's address only
- [ ] One backend process per database
- [ ] Database private, dedicated role, backups encrypted and kept apart from keys
- [ ] `letmehandle-purge` scheduled at least daily, with alerting on failure
- [ ] Provider credentials dedicated to this deployment and scoped
- [ ] JSON logs at `info`, access restricted, retention decided
- [ ] Image rebuilt and dependencies audited on a schedule
