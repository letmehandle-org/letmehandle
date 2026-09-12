# Setup

## What you need

| Tool | Version | Why |
| --- | --- | --- |
| Python | 3.12 | backend. Pinned in `.python-version`. |
| uv | latest | Python dependencies and virtual environment |
| Node | 22 | mobile tooling. Pinned in `.nvmrc`. |
| pnpm | 9 | workspace package manager |
| Docker | latest | PostgreSQL, and the backend image |
| Xcode | latest | iOS builds. macOS only. |
| Android Studio | latest | Android builds |

You need only Python, uv, Node, pnpm and Docker to work on the backend.

## First run

```bash
git clone https://github.com/letmehandle-org/letmehandle.git
cd letmehandle
make setup
```

`make setup` installs both dependency trees and, importantly, the git hooks. Hooks are not
cloned with a repository, so without this step nothing checks your commits until CI does.

```bash
cp .env.example .env
```

Fill in `.env`. The backend validates it at startup and refuses to run with a message naming
the variable rather than failing later somewhere confusing.

```bash
make up
curl localhost:8000/health
```

## Working

```bash
make verify      # everything: audit, lint, typecheck, test, coverage. What CI runs.
make test        # just the tests
make format      # apply formatting
make down        # stop the local services
```

`make verify` is the same set of commands CI runs. If it passes locally it passes in CI,
which is the point of routing everything through the Makefile.

## The mobile app

```bash
pnpm --filter mobile ios       # simulator
pnpm --filter mobile android   # emulator
```

Bare React Native: `ios/` and `android/` are part of the repository and are yours to change.
After changing native dependencies, `cd apps/mobile/ios && pod install`.

## Troubleshooting

**`make setup` fails on the Python step.** Check `python3 --version` is 3.12. uv can install
it for you: `uv python install 3.12`.

**The backend will not start.** It names the configuration variable it is unhappy about. If it
names none, the database is probably not up: `make up`.

**A commit is rejected.** The hooks explain what they found. They run before anything is
permanent, which is the only moment the fix is free.

**Tests need a phone number.** Use a range reserved for fiction — `+1 NPA 555-01xx`, or
`+44 7700 900xxx`. The audit rejects anything else.
