# Phase 0 — Project foundation

**Goal:** a clean, public, continuously verified monorepo that builds and runs, with no
product behaviour in it.

## In scope

### Repository
- Directory layout as specified: `apps/`, `packages/`, `tests/e2e/`, `infra/`, `docs/`,
  `scripts/`, `.github/`.
- `.gitignore`, `.editorconfig`, `.gitattributes`, `.nvmrc`, `.python-version`.
- Root `Makefile` as the single entry point. Every gate a contributor or CI runs is a
  target here, so the two can never drift: `setup`, `verify`, `test`, `lint`, `format`,
  `typecheck`, `coverage`, `hooks`, `up`, `down`.
- pnpm workspace definition (D-001).

### Backend — `apps/backend`
- `pyproject.toml`, dependencies managed by uv, Python pinned per D-001.
- FastAPI application with an explicit lifespan: resources are acquired on startup and
  released on shutdown, and shutdown runs on every exit path.
- Layout that the later phases slot into without being moved:
  `src/letmehandle/{domain,application,adapters,api,config,observability}`.
- Configuration: a typed settings object loaded from the environment, validated at startup.
  The process refuses to start on invalid or missing required configuration, with a message
  naming the variable. No `os.environ` access anywhere outside the config module.
- Structured logging: JSON in production, human-readable in development, one configuration
  point, request-scoped correlation id on every log line.
- `GET /health` — liveness, no dependencies touched.
- `GET /health/ready` — readiness, asserts the database is reachable.
- Error handling: a single exception-to-response mapping. Unhandled exceptions return a
  correlation id and never a stack trace.

### Mobile — `apps/mobile`
- Bare React Native with TypeScript (D-018), bundle id and application id
  `org.letmehandle.app`, display name `LetMeHandle`.
- `ios/` and `android/` committed.
- Navigation foundation with one placeholder screen. Navigation is typed; there are no
  string route names at call sites.
- Environment configuration read once into a typed config module, never scattered.
- i18n layer wired from the first screen (D-017). No literal user-facing string in a
  component.
- Jest configured with coverage reporting.

### Local environment
- `docker-compose.yml`: PostgreSQL and the backend. No service is added because it might
  be needed later.
- `infra/docker/backend.Dockerfile`: multi-stage, non-root user, no build toolchain in the
  final image, healthcheck.
- Alembic initialised with no migrations yet. Phase 2 writes the first one.

### Quality gates
- Backend: ruff (lint + format), mypy in strict mode, pytest with coverage, import-linter
  contracts asserting the domain layer imports nothing from `adapters` or any vendor SDK
  (D-003).
- Mobile: ESLint, Prettier, `tsc --noEmit`, Jest.
- `scripts/disclosure_audit.py` and `scripts/pii_audit.sh` (D-021).
- Hooks installed by `make hooks`: `pre-commit` (format, lint, secret and disclosure scan
  on staged content), `commit-msg` (Conventional Commits + disclosure scan), `pre-push`
  (full verify against the commit being pushed, not the working tree).
- gitleaks configuration, with an allowlist that is path-scoped and initially empty.

### CI — `.github/workflows`
- `ci.yml` — backend lint, typecheck, test, coverage floor; mobile lint, typecheck, test,
  coverage floor; disclosure and PII audits; runs on every pull request.
- `ios-build.yml` — builds the iOS app, triggered only by changes under `apps/mobile/**`.
- `android-build.yml` — same for Android, same path filter.
- `codeql.yml`, `dependency-review.yml`, `scorecard.yml`.
- `dependabot.yml` for pip, npm, GitHub Actions, Docker.

### Open-source metadata
- `README.md` — what it is, what it is not yet, and setup instructions that work from a
  fresh clone.
- `LICENSE` (MIT), `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `CHANGELOG.md`.
- `SECURITY.md` directs reports to GitHub private security advisories. It carries no email
  address (D-021).
- `.github/ISSUE_TEMPLATE/` (bug, feature, provider request, config) and
  `PULL_REQUEST_TEMPLATE.md`.
- `.env.example` — every variable the backend reads, with empty values and a comment each.
- `docs/architecture/overview.md`, `docs/development/setup.md`,
  `docs/providers/README.md` describing the extension model.

## Explicitly out of scope

- Any domain type. `domain/` exists as a directory with no product concepts in it.
- Any provider interface or implementation. Phase 1 defines them; Phase 0 only proves that
  a vendor import from the domain layer fails the build.
- Database tables. Alembic is configured; the first migration is Phase 2's.
- Authentication, users, calls, agents, audio.
- Terraform. `infra/terraform/` holds a README stating it is unused until a deployment
  target exists.
- `packages/`. Empty by D-002, with a README explaining the rule.

## Tests required

| Kind | Must prove |
| --- | --- |
| Unit | Config rejects invalid input and names the offending variable; log records carry the correlation id; the exception mapper produces the documented shape. |
| Integration | `/health` responds without touching the database; `/health/ready` fails when the database is unreachable and succeeds when it is; the app starts and shuts down cleanly, releasing the pool. |
| Static | import-linter fails when a vendor import is added to `domain/`. Asserted by a test that adds one in a temporary module. |
| Mobile | The app renders; navigation moves between two screens; config parses; a missing translation key fails the test rather than rendering the key. |

## Acceptance criteria

1. A fresh clone, following `README.md` only, reaches a running backend and a built mobile
   app on a machine with no project-specific prior setup.
2. `make verify` runs every gate and passes.
3. `GET /health` returns 200. `GET /health/ready` returns 200 with the database up, and a
   non-200 with it down.
4. The backend starts and stops with no leaked connections and no unhandled task warnings.
5. The mobile app builds for iOS and for Android.
6. CI passes on a pull request.
7. Coverage meets the D-020 floors for the code that exists.
8. Adding `import boto3` to a domain module fails `make verify`.
9. Committing a string shaped like a credential fails at the pre-commit hook.
10. Committing a message containing a phone number or an email address fails at commit-msg.
11. `docker compose up` brings up the database and the backend, and readiness turns green.
12. No file tracked in the repository contains a real credential, account identifier, or
    personal datum.

## Risks and open questions

- **Bare React Native setup cost.** `ios/` and `android/` make a fresh clone heavier to set
  up than an Expo project. Mitigated by `make setup` doing the work and `docs/development/setup.md`
  stating exact tool versions. Accepted as the cost of D-018.
- **The 98% floor with almost no code.** Early on, a handful of uncovered lines is a large
  percentage. The floor is enforced from the first commit anyway; it is easier to hold than
  to reach later.
- **macOS CI minutes.** Free for public repositories. The path filter keeps mobile builds
  off backend-only pull requests regardless.

---

# Phase 0 verification

```
PHASE 0 VERIFICATION

Planned tasks:        complete
Acceptance criteria:  12/12 passed
Unit tests:           passed   apps/backend: uv run pytest — 51 passed
                               apps/mobile:  pnpm test — 22 passed, 5 suites
Integration tests:    passed   included in the 51 above: health, readiness against a live
                               database, lifespan symmetry, import boundaries
E2E tests:            not applicable — there is no product behaviour to exercise yet
Coverage:             100.00%  backend, floor 98
                      100% statements / 95.65% branches mobile logic, floor 90
Lint:                 passed   ruff check; eslint --max-warnings 0
Format:               passed   ruff format --check; prettier --check
Typecheck:            passed   mypy --strict over src and tests; tsc --noEmit
Static analysis:      passed   import-linter, 3 contracts kept
Build:                passed   backend image builds; iOS and Android both build in CI
Application runs:     yes      docker compose up; /health 200, /health/ready 200 against
                               PostgreSQL; correlation id present on every response and log line
Manual verification:  a clean clone of the public repository, following README only, reached a
                      running backend and a passing `make verify`. The gates were each tested by
                      deliberately breaking them: a staged credential, a commit message carrying
                      an address and a phone number, a non-conventional subject, and a vendor
                      import added to the domain layer. Each was rejected.
Docs updated:         README, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, CHANGELOG, architecture
                      overview, decision record, setup, providers, both .env.example files
Known issues:         two npm advisories in the build toolchain remain open and are recorded in
                      the phase 12 plan rather than inherited silently: `image-size`, which has
                      no patched version published, and `decode-uri-component`, whose patched
                      version is ESM only and breaks the test runner. Neither reaches a running
                      application.
Commits created:      30 on main
```

## Acceptance criteria, each with its evidence

| # | Criterion | Evidence |
| --- | --- | --- |
| 1 | A fresh clone reaches a running system from the README alone | Cloned the public repository into a clean directory; `make setup`, `cp .env.example .env`, `docker compose up` produced a healthy stack and `make verify` passed, with no step the README does not state |
| 2 | `make verify` runs every gate and passes | Run on main and required on every pull request |
| 3 | Health and readiness behave differently | `/health` 200 with no database configured; `/health/ready` 503 without one and 200 with PostgreSQL up |
| 4 | Clean startup and shutdown, nothing leaked | `test_lifecycle.py` asserts the pool is disposed on the success path **and** when the body raises |
| 5 | The mobile app builds for iOS and for Android | Both jobs green in CI on #17. iOS also verified locally (`BUILD SUCCEEDED`); Android could not be built locally because this machine has no Android SDK, so CI is the evidence |
| 6 | CI passes on a pull request | #12, #17, #19 |
| 7 | Coverage meets the floors | 100% backend, 100%/95.65% mobile logic |
| 8 | A vendor import in the domain layer fails the build | `test_import_boundaries.py` writes one and asserts the contracts break, for a driver, a framework and an adapter |
| 9 | A staged credential is rejected at commit | Verified by hand; gitleaks also runs over the whole history in CI |
| 10 | A commit message carrying personal data is rejected | Verified by hand with an address and a phone number |
| 11 | `docker compose up` brings the stack up and readiness turns green | Verified locally and from the clean clone |
| 12 | No tracked file holds a credential, account identifier or personal datum | `scripts/disclosure_audit.py` clean over the tree; gitleaks clean over all 35 commits |

## What was found and fixed along the way

Each of these was a defect the gates or the tests caught, not something noticed by reading.

- **The correlation id was missing from exactly the response that needed it most.** An unhandled
  exception is turned into a response by Starlette's outermost error middleware, which runs
  after ours has unwound and reset the context variable. A 500 therefore carried no id. The id
  is now also written to the request scope, which outlives the middleware.
- **Android could not build in this repository at all.** The template resolves
  `../node_modules`, which does not exist in a pnpm workspace. Paths are now resolved by asking
  node, as the Podfile always did — which is why only Android was affected.
- **The iOS toolchain could not run on CI.** macOS ships Ruby 2.6, whose bundler is recorded in
  `Gemfile.lock` and calls a method removed from the language in Ruby 3.2.
- **The disclosure gate had three defects of its own**, each found by it blocking legitimate
  work: it matched the userinfo of a database URL as an address, it blocked every automated
  dependency commit on the forge's own service address, and its argument parsing could not
  accept a rev range containing `--not`.
- **Six advisories were being held open by two version pins** inherited from the template, for
  incompatibilities that no longer exist.
