# Demonstration

A walkthrough of what the project does today, end to end, with no paid account. Part one runs the
backend and signs in. Part two runs whole calls — the assistant handling one, an escalation into the
live call, a handset screening one — against simulated providers. Part three is the mobile app. Part
four is what needs real accounts, and is not a demonstration anybody can run from a clone.

Every command runs from the repository root unless it says otherwise. If you started the stack on
other ports, use them in place of `8000` and `5432` below.

## 1. The backend, signed in

```bash
make setup
make sample-env
make up
curl -s localhost:8000/health
curl -s localhost:8000/health/ready
```

If you followed the quick start in the README, this is already done, and running it again changes
nothing: `make sample-env` leaves an existing `.env` alone. It writes one with fresh keys, the mock
sign-in provider and example voices, and nothing that costs money. `make up` builds the backend image, starts PostgreSQL, migrates it and
starts the backend. Liveness answers `{"status":"ok",...}`; readiness answers `ready` once the
database is reachable.

Sign in. The mock provider sends nothing and accepts `123456` for every number; the number below is
from a range reserved for fiction.

```bash
B=http://localhost:8000
CHALLENGE=$(curl -s -X POST $B/v1/auth/challenge -H 'content-type: application/json' \
  -d '{"phone_number":"+12025550123"}')
CHALLENGE_ID=$(printf '%s' "$CHALLENGE" | python3 -c 'import json,sys; print(json.load(sys.stdin)["challenge_id"])')
TOKENS=$(curl -s -X POST $B/v1/auth/verify -H 'content-type: application/json' \
  -d "{\"challenge_id\":\"$CHALLENGE_ID\",\"code\":\"123456\"}")
ACCESS=$(printf '%s' "$TOKENS" | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
```

Then look around as that user:

```bash
curl -s -w '\n' $B/v1/me -H "authorization: Bearer $ACCESS"
curl -s -w '\n' $B/v1/onboarding -H "authorization: Bearer $ACCESS"
curl -s -w '\n' $B/v1/preferences -H "authorization: Bearer $ACCESS"
curl -s -w '\n' $B/v1/voices -H "authorization: Bearer $ACCESS"
curl -s -w '\n' $B/v1/calls -H "authorization: Bearer $ACCESS"
```

What each shows:

- **`/v1/me`** — the profile, and `call_forwarding: null`: this deployment has no call transport, so
  nothing needs forwarding (D-034).
- **`/v1/onboarding`** — the four setup steps still to do (D-032).
- **`/v1/preferences`** — the defaults every section starts from.
- **`/v1/voices`** — the example catalogue, and capabilities with `preview: false`: the provider has
  no samples, so no preview route exists at all (D-024). `curl -s -o /dev/null -w '%{http_code}'
  $B/v1/voices/example-voice-a/preview -H "authorization: Bearer $ACCESS"` answers `404`.
- **`/v1/calls`** — an empty history; nothing carries calls here.

The API's own documentation is at `http://localhost:8000/docs` while `APP_ENV` is not `production`.

## 2. Whole calls, simulated

The end-to-end scenarios run the application as a deployment runs it — its settings, its lifespan,
its orchestrator — over a simulated telephony provider, a simulated speech service, a scripted model
and recording push providers. They need the database from part one.

```bash
make e2e
```

To watch one story at a time:

```bash
cd apps/backend
TEST_DATABASE_URL=postgresql+asyncpg://letmehandle:letmehandle@127.0.0.1:5432/letmehandle \
  uv run pytest tests/e2e/test_escalation.py -v
```

| Scenario file | The story |
| --- | --- |
| `test_assistant_calls.py` | a routine call the assistant resolves; an important contact put straight through; a caller asking for something the assistant was never allowed to do, and telling it to ignore its instructions |
| `test_escalation.py` | a delivery driver the user must decide for: the notification, the user's phone ringing, the user joining the live call; and the user not answering, with the assistant taking the call back |
| `test_handset_screening.py` | calls decided on an Android handset and reported afterwards |
| `test_capability_mismatch.py` | a transport that cannot add the user: no escalation path exists, the assistant stays with the caller, and the reason the user was wanted is kept |
| `test_provider_faults.py` | every callback delivered twice and out of order; the model down; a restart mid-call |
| `test_speech_failures.py` | the speech service refusing the connection, dropping mid-utterance, and refusing the reconnection; the caller is never left on a silent line |

Each scenario asserts what the caller heard, what the user was sent, the call's final state and its
summary.

## 3. The mobile app

With the backend from part one running:

```bash
cp apps/mobile/.env.example apps/mobile/.env
pnpm --filter mobile ios         # or: pnpm --filter mobile android
```

On an Android emulator, set `API_BASE_URL` in `apps/mobile/.env` to `http://10.0.2.2:8000`. Sign in
with any number from a fictional range and the code `123456`, then go through setup. The toolchain
each platform needs is in [`setup.md`](setup.md#the-mobile-app).

## 4. With real accounts

A real call answered by the assistant, a real escalation to a real phone, and native screening on a
real Android phone need a telephony account, a speech service, a model endpoint, push credentials
and three phones. That run is scripted in
[`docs/testing/manual-verification.md`](../testing/manual-verification.md). It has not yet been
performed; see [`PLAN.md`](../../PLAN.md) for what is held.

## Stopping

```bash
make down
```

`docker compose down -v` also removes the database volume.
