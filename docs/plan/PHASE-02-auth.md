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
