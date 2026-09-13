# Setup

## What you need

| Tool | Version | Needed for |
| --- | --- | --- |
| Python | 3.12 | backend. Pinned in `.python-version`. |
| uv | 0.5 or later | Python dependencies and the virtual environment |
| Node | 22 or later | mobile tooling. `.nvmrc` names 22. |
| pnpm | 9 | the workspace |
| Docker | with Compose 2.20 or later | PostgreSQL, the migrations, and the backend image |
| Xcode | 16 or later | iOS builds. macOS only. |
| Ruby | 3.4 or later | CocoaPods, for iOS. macOS ships 2.6, which is too old. |
| Android Studio | any recent, with JDK 17 | Android builds |

Only Python, uv, Node, pnpm and Docker are needed to work on the backend.

## First run

```bash
git clone https://github.com/letmehandle-org/letmehandle.git
cd letmehandle
make setup
```

`make setup` installs both dependency trees and the git hooks. Hooks are not cloned with a
repository, so without this step nothing checks your commits until CI does.

```bash
make sample-env
cp apps/mobile/.env.example apps/mobile/.env
```

`make sample-env` copies `.env.example` to `.env` and fills in the two keys the backend cannot start
without — `AUTH_SIGNING_KEY` and `TRANSCRIPT_ENCRYPTION_KEYS` — with fresh random values. It refuses
to replace an existing `.env`. Everything else is left as the example has it: the mock sign-in
provider, example voices, and no speech service, model, call transport or push credentials, so
nothing costs money. Every variable is in the
[configuration reference](configuration.md).

Neither example file carries a real value, and neither ever will (D-021). The backend validates
its configuration at startup and refuses to run with a message naming the offending variable;
the mobile app does the same.

**Signing in while testing.** With the default mock code provider (`OTP_PROVIDER=mock`) no text
message is sent and every sign-in code is `123456`; a development build of the app says so on
the code screen. This is for testing only and is removed before launch. It cannot reach
production: the mock provider refuses to start there.

```bash
make up
curl localhost:8000/health
curl localhost:8000/health/ready
```

`make up` builds the backend image, starts PostgreSQL, runs the migrations to the latest one, and
starts the backend once they have succeeded. The first build takes a few minutes.

`/health` answers whether the process is alive and touches nothing. `/health/ready` answers
whether it can reach the database, and returns 503 when it cannot.

### If port 8000 or 5432 is already taken

Both host ports are configurable, because they are popular ports and a clash should not require
editing a tracked file:

```bash
BACKEND_PORT=8100 POSTGRES_PORT=5433 make up
curl localhost:8100/health
```

Set them in your shell, not in `.env`, to make it permanent: `make` exports its own defaults for
both, and a variable exported to Docker Compose wins over the same variable in `.env`. Use the same
values for every `make` command, so `make verify` finds the database where `make up` put it.

## Working

```bash
make verify      # audit, lint, format, types, boundaries, tests, coverage, generated files
make test        # just the tests
make format      # apply formatting
make down        # stop the local services
```

`make verify` runs what CI runs. More on the suites and their gates in [`testing.md`](testing.md),
on branches, commits and releases in [`workflow.md`](workflow.md).

## The backend on its own

```bash
cd apps/backend
uv run pytest                 # tests
uv run pytest --cov           # with coverage
uv run mypy src tests         # types
uv run lint-imports           # the architecture boundaries
```

To run the server outside Docker — to attach a debugger, say — stop the container that holds its
port, and point the process at the root `.env` and the database the stack started:

```bash
docker compose stop backend
cd apps/backend
DATABASE_URL=postgresql+asyncpg://letmehandle:letmehandle@127.0.0.1:5432/letmehandle \
  uv run --env-file ../../.env letmehandle
```

The settings read `.env` from the working directory, which is `apps/backend` here, hence
`--env-file`. A variable already set in the shell wins over the file.

### Tests that need a database

The suite talks to a real PostgreSQL, because the behaviour it relies on — unique constraints,
cascading deletes, the row count a bulk update reports, timestamps that keep their timezone —
differs in a substitute. `make verify` refuses to run without one rather than letting the
coverage floor look like a failure.

Each run works in a schema of its own, named after its process. Two runs against one database
therefore do not collide, which matters more than it sounds: without it, a second run arriving
midway through the first drops the tables the first is asserting on, and the failure looks
exactly like a real defect in unrelated code.

If you have something else on 5432:

```bash
POSTGRES_PORT=5433 make up verify
```

### Migrations

`make up` runs them. By hand, against the stack's database:

```bash
cd apps/backend
export DATABASE_URL=postgresql+asyncpg://letmehandle:letmehandle@127.0.0.1:5432/letmehandle
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "what it does"
uv run alembic check                    # fails if the models and the migrations disagree
```

`alembic check` runs in CI. A migration written by hand against models that have moved on is a
migration that passes and leaves the database wrong.

Alembic reads the database URL from the application's own settings, so there is one place this
project learns where its database is. The backend image carries the migrations, so a deployment of
it runs `alembic upgrade head` from `/app` before starting a new version.

## The mobile app

```bash
pnpm --filter mobile ios       # simulator
pnpm --filter mobile android   # emulator
pnpm --filter mobile test
```

Bare React Native: `ios/` and `android/` are part of the repository and are yours to change.

**iOS**, first run and after any native dependency changes:

```bash
cd apps/mobile
bundle install
cd ios && bundle exec pod install
```

The Ruby toolchain is pinned by `Gemfile.lock` and installs into `apps/mobile/vendor`, which is
ignored.

macOS ships Ruby 2.6, which is too old: its bundler calls a method removed from the language in
3.2, and the failure names neither Ruby nor bundler. Install a current one — `brew install ruby`
— and put it ahead of the system's on your `PATH`.

If CocoaPods fails with `Unicode Normalization not appropriate for ASCII-8BIT`, your shell has no
UTF-8 locale. Run it with `LANG=en_US.UTF-8`. That error is CocoaPods failing while reporting a
different error, so it never says what actually went wrong.

**Android** needs JDK 17. A newer JDK will fail in ways that do not name the cause.

The first Android build compiles native code for every processor architecture, which takes a
long time. For a single emulator or device, build only the one it uses:

```bash
pnpm --filter mobile android --active-arch-only
```

**Reaching the backend from a device or simulator.** On an iOS simulator `localhost` is the host
machine. On an Android emulator the host is `10.0.2.2`. Set `API_BASE_URL` in
`apps/mobile/.env` accordingly.

## Troubleshooting

**`make setup` fails on the Python step.** Check `python3 --version` is 3.12. uv will install it:
`uv python install 3.12`.

**The backend will not start.** It names the variable it is unhappy about; `docker compose logs
backend` shows it when it runs in the stack. `AUTH_SIGNING_KEY is required` means `.env` was copied
by hand rather than written by `make sample-env`. If it names none, the database is probably not
up: `make up`.

**`make up` says `service "migrate" didn't complete successfully`.** `docker compose logs migrate`
says why. A stack left half-started by an earlier failure can hold a stale network; `make down` and
`make up` again.

**A commit is rejected.** The hooks say what they found. They run before anything is permanent,
which is the only moment the fix is free. See D-021 for what they look for.

**Tests need a phone number.** Use a range reserved for fiction — `+1 NPA 555-01xx`, or
`+44 7700 900xxx`. The audit rejects anything else, including your own number.

**Metro cannot find a module.** The workspace uses hoisted linking (`.npmrc`) because Metro and
the native build systems do not understand pnpm's symlinked store. If you have changed that
setting, change it back. Hoisting puts the packages in the workspace root, which Metro only sees because
`apps/mobile/metro.config.js` adds it to `watchFolders`; keep it there.

**The Android app stops at startup naming a missing variable** although `apps/mobile/.env` sets
it. The values reach Android through `BuildConfig`, generated by the `dotenv.gradle` line in
`apps/mobile/android/app/build.gradle`. Rebuild after changing `.env` — it is read at build
time, not at launch.
