# Contributing

Thank you for considering it. This document is short because the rules are few and they are
enforced by the build rather than by review.

## Setting up

```bash
make setup     # installs dependencies and the git hooks
make verify    # everything CI will run
```

`make setup` installs the git hooks. They are not cloned with the repository, so this step is
not optional — the hooks are what stop a mistake becoming permanent.

## The rules

**Small commits.** One logical change each, each leaving the repository working, each with
its tests. A pull request is squashed when it merges, so keep the pull request itself to one
logical change and the history on `main` stays revertible.

**Conventional commits.** `type(scope): summary`, 72 characters or fewer. Enforced by the
commit-msg hook. Types: `feat fix refactor perf test docs build ci chore revert`.

**Branch, then pull request.** `feature/*`, `fix/*`, `refactor/*`, `docs/*`. No direct
commits to `main`.

**Tests come with the change.** Coverage floors are 98% for the backend and 90% for mobile
logic. They are floors, not targets — a test written only to move the number will be asked
about in review.

**Types are strict.** mypy strict on the backend, no `any` in TypeScript.

## The architecture rules

These are the ones worth reading before you write code, because the build will reject a
change that breaks them and the reason may not be obvious:

- **The domain layer imports nothing external.** No vendor SDK, no HTTP framework, no
  database driver. Enforced by import-linter.
- **Provider behaviour stays in its adapter.** If domain code needs to know which provider is
  configured, the interface is wrong. Add a capability, not a branch.
- **No feature is presented that the configured provider cannot do.** Capabilities are
  declared and the interface renders from them.
- **No unfinished code.** No `TODO`-driven stubs, no commented-out code, no dead code, no
  exception swallowed silently.

Decisions that shaped these are in [`docs/architecture/decisions.md`](docs/architecture/decisions.md),
numbered and citable.

## What must never be committed

Credentials, tokens, keys. Cloud or provider account identifiers. Names of deployed
resources. Personal data of any kind — names, addresses, email addresses, real phone numbers.

Test fixtures that need a phone number must use a range reserved for fiction: `+1 NPA
555-01xx`, or the United Kingdom's `+44 7700 900xxx`. The audit rejects anything else,
including your own number.

This is checked at commit, at push, and in CI. See `scripts/disclosure_audit.py`, and D-021.

## Adding a provider

Implement the port, pass the existing contract suite for it, declare your capabilities
honestly, and add a page under `docs/providers/`. You should not need to change any core code
— if you do, that is a bug in the interface and worth raising before you work around it.

## Reporting a vulnerability

Not here. See [`SECURITY.md`](SECURITY.md).
