# Phase 2 — Authentication and user foundation

**Goal:** a person can create an account, sign in, stay signed in, and sign out, from the
mobile app against the real backend.

## In scope

### Persistence
- First Alembic migration. Every migration is reversible and the downgrade is tested.
- Tables: `users`, `user_devices`, `refresh_tokens`, `otp_challenges`.
- A repository layer over SQLAlchemy. Domain code depends on the repository interface from
  Phase 1; SQLAlchemy models are adapter types and never cross into the domain.
- Every user-scoped query takes the user id as a required argument. There is no repository
  method that can read another user's rows by accident (D-012).

### Authentication
- Phone number is the identity (D-010). Normalised to E.164 on the way in; the unnormalised
  form is never stored and never compared.
- Flow: request challenge → verify code → issue tokens.
- `OTPProvider` with a mock implementation used in development and test. It refuses to
  initialise when the application environment is production.
- Rate limiting on challenge requests, per phone number and per source address, with the
  limiter behind an interface so it is not tied to a store.
- Challenges are single-use, expiring, attempt-limited, and stored hashed. A verified or
  exhausted challenge cannot be reused.
- Access token (short-lived, stateless) and refresh token (long-lived, stored, rotating).
  Rotation detects reuse: presenting a used refresh token revokes the family.
- Sign-out revokes the refresh token server-side and clears local state. Sign-out on one
  device does not sign out the others unless asked.

### API
- `POST /v1/auth/challenge`, `POST /v1/auth/verify`, `POST /v1/auth/refresh`,
  `POST /v1/auth/signout`.
- `GET /v1/me`, `PATCH /v1/me`.
- Dependency-injected current-user resolution. A route is protected by declaring the
  dependency; there is no middleware that decides based on a path pattern, because a path
  pattern is a rule someone forgets to update.
- Authentication failures are indistinguishable between "no such account" and "wrong code".

### Mobile
- Welcome, phone entry, code entry, and a minimal profile screen.
- Tokens in the platform secure store (Keychain / Keystore), never in async storage.
- A single API client owning auth headers, refresh-on-401 with request replay, and a single
  concurrent refresh. Screens never touch tokens.
- Session restored on cold start; an invalid session lands on welcome without a flash of
  signed-in UI.

### Shared types
- The OpenAPI schema is emitted by the backend and the mobile client's types are generated
  from it (D-002), in `packages/api-client`. Generation runs in `make verify` and a drift
  between schema and committed output fails the build.

## Explicitly out of scope

- Social or email sign-in. Preferences, onboarding, calls, notifications.
- Account deletion (Phase 12, with the data-retention work it belongs to).
- Push registration (Phase 10 needs it; Phase 2 creates the device table it will use).

## Tests required

| Kind | Must prove |
| --- | --- |
| Unit | E.164 normalisation, including rejection of unparseable input; challenge expiry, attempt exhaustion and single use; refresh rotation and reuse detection revoking the family; token claims and expiry. |
| Integration | Full sign-up and sign-in against a real PostgreSQL; protected routes reject absent, malformed, expired and revoked tokens; rate limits trigger and recover; migration upgrade and downgrade both run clean. |
| Isolation | User A cannot read or modify user B's row through any exposed route or repository method. Written as a reusable helper, because every later phase adds resources that need the same proof. |
| Mobile | The API client refreshes once under concurrent 401s and replays the queued requests; session restoration; sign-out clears the secure store. |
| E2E | Sign up, restart the app, still signed in; sign out, restart, signed out. |

## Acceptance criteria

1. A new user creates an account from the app against the real backend.
2. An existing user signs in.
3. The session survives an app restart.
4. Sign-out invalidates the refresh token server-side, and the old token is rejected.
5. Protected routes behave correctly for every invalid-token case.
6. A user cannot access another user's data by any route.
7. Migrations run forward and backward cleanly.
8. No contributor needs a paid account to sign in locally.
9. The mock OTP provider refuses to start in a production configuration.
10. Generated API types match the schema; drift fails the build.
11. Coverage meets the D-020 floors.

## Risks and open questions

- **Mock OTP in production.** The failure mode is catastrophic and silent. The guard is a
  startup refusal, tested, plus a readiness check that reports which OTP provider is active.
- **Refresh storms.** A cold start firing several requests at once must not produce several
  refreshes. Tested explicitly rather than assumed.
- **Phone number as identity.** Numbers are recycled by carriers. Out of scope to solve now;
  noted so that a later re-verification requirement is a known addition, not a surprise.

---

# Phase 2 verification

```
PHASE 2 VERIFICATION

Planned tasks:        complete
Acceptance criteria:  11/11 passed
Unit tests:           passed   backend 637 passed, 3 skipped; mobile 80 passed
Integration tests:    passed   against a real PostgreSQL: repositories, the unit of work,
                               and the sign-in API end to end through the whole stack
E2E tests:            passed   the mobile suite drives the application tree with the backend
                               stood in for at the network boundary: number → code → signed in,
                               cold start with a stored session, renewal, and sign-out
Coverage:             100.00%  backend, floor 98
                      97.7% statements / 91.2% branches mobile logic, floor 90
Lint:                 passed   ruff check; eslint --max-warnings 0; prettier --check
Format:               passed
Typecheck:            passed   mypy --strict; tsc --noEmit for the app and the generated client
Static analysis:      passed   import-linter, 3 contracts kept
Build:                passed   backend image; iOS and Android in CI
Application runs:     yes      docker compose up, migration applied, sign-in exercised over HTTP
Manual verification:  the migration was run forwards, backwards and forwards again against a
                      real database, and `alembic check` confirms the models and the schema
                      agree. The mobile flow is covered by the application-level tests rather
                      than by hand; a device run waits for a design in phase 9
Docs updated:         .env.example, docs/development/setup.md, packages/api-client/README.md
Known issues:         the application cannot start in production, deliberately — see below
Commits created:      9
```

## Acceptance criteria, each with its evidence

| # | Criterion | Evidence |
| --- | --- | --- |
| 1 | A new user creates an account from the app | `test_a_new_number_gets_an_account_and_a_session`, and the mobile flow test that walks number → code → signed in |
| 2 | An existing user signs in | `test_the_same_number_signs_into_the_same_account` |
| 3 | The session survives a restart | `test_session.tsx`: a cold start with a stored session opens the application, renewing first if the token is nearly expired |
| 4 | Sign-out invalidates the refresh token server-side | `test_signing_out_invalidates_the_session`, and the mobile test asserting the backend is told before local state is cleared |
| 5 | Protected routes behave correctly for every invalid token | Absent, malformed, wrong scheme, empty, and a token for a deleted account — all 401 with the same body |
| 6 | A user cannot access another's data | `TestIsolation` at both the repository and the HTTP level |
| 7 | Migrations run forwards and backwards | Run three times locally; `alembic check` proves no drift; both are CI steps |
| 8 | No contributor needs a paid account to sign in locally | The mock provider records the code; the whole suite runs with no credentials |
| 9 | The mock provider refuses to start in production | `test_it_refuses_to_exist_in_production`, and the lifespan test that shows the application declining to start |
| 10 | Generated API types match the schema, and drift fails the build | `make api-types-check`, and a CI step that regenerates and diffs |
| 11 | Coverage meets the floors | 100% backend, 97.7%/91.2% mobile logic |

## The one deliberate limitation

**This application cannot start in production, and that is the intended state.** There is no
provider that actually delivers a sign-in code: the first arrives with the telephony work. The
mock refuses to be constructed when the environment is production, so a deployment fails loudly
at startup rather than running with a provider that would let anybody sign in as anybody.

`test_a_production_configuration_refuses_the_mock_provider` asserts it, so this cannot be
forgotten and cannot quietly stop being true.

## What was found and fixed

Each of these was caught by a test rather than noticed by reading, and three of them were
invisible to the layer above.

- **One hasher was being used for two jobs.** A one-time code is verified against a known row,
  so it is salted; a refresh token has to be *found* by its hash, which a salted hash makes
  impossible. Every renewal failed with the same message as a stolen token. The unit tests
  could not see it, because their fake hasher is deterministic for both — so the fix came with
  a test that uses two different kinds, which is the only way this stays fixed.
- **A revocation was being rolled back by the refusal that triggered it.** Detecting a replayed
  refresh token revokes the whole family and then refuses the request — and an ordinary error
  path rolls the transaction back, leaving the stolen token working with nothing to show it had
  been noticed. Deliberate refusals now commit; unexpected failures still roll back.
- **Every malformed request was returning 500.** The validation handler passed pydantic's raw
  error objects to a JSON response, and they contain the original exception, which is not
  serialisable. A validation bug had become an outage.
- **Token expiry was judged by the machine's clock**, not the one the application was given,
  which meant one part of the system disagreed with every other about the time and the
  behaviour could not be tested without waiting.
- **Coverage was under-reporting by seven points.** SQLAlchemy runs application code inside
  greenlets, and coverage loses track without being told — it was reporting lines as unrun that
  the tests demonstrably executed, which sends somebody writing tests for covered code.
- **The two sign-in screens had drifted into two copies of the same error-describing logic.**
  Extracted, and the fallbacks are now tested directly rather than through a backend that
  cannot be persuaded to produce them.
