# Workflow

How a change gets from a branch to `main`, and from `main` to a release. The rules are few, and the
build enforces them rather than review.

## Branches (D-019)

```
feature/* | fix/* | refactor/* | docs/*  →  pull request  →  main
```

`main` is always buildable. Nothing is committed to it directly. A pull request is squash-merged,
so keep each one to a single logical change and every commit on `main` stays revertible on its own.

## Commits

- **Conventional.** `type(scope): summary`, 72 characters or fewer. Types: `feat fix refactor perf
  test docs build ci chore revert`. The `commit-msg` hook refuses anything else.
- **Small.** One logical change each, each leaving the repository working, each with its tests.
- **Nothing private.** No credential, account identifier, resource name, real phone number or
  personal datum in a file, a commit message, or a pull request title or body (D-021). The
  `pre-commit`, `commit-msg` and `pre-push` hooks check, and so does CI over the whole history.
  Fixtures use `+1 NPA 555-01xx` or `+44 7700 900xxx`, and `example.com`, `.invalid` or `.test`.

The hooks are installed by `make setup` (or `make hooks`). They are not cloned with the repository,
so without that step nothing checks a commit until CI does.

## A pull request

1. Open an issue first for anything bigger than a fix; the [templates](../../.github/ISSUE_TEMPLATE)
   ask what is needed. A new provider starts with a provider support issue.
2. Branch from `main`.
3. Run `make verify` before pushing. The `pre-push` hook runs the disclosure audit; CI runs the rest.
4. Fill in the [pull request template](../../.github/PULL_REQUEST_TEMPLATE.md): what changes, which
   phase it belongs to, how it was verified.
5. Add a line under `## [Unreleased]` in `CHANGELOG.md` for anything a user or a contributor would
   notice.

CI on a pull request runs the disclosure audit over the pull request's commits, title and body,
gitleaks over the whole history, the backend suite with PostgreSQL and the coverage floor,
migrations up and down, the mobile suite, the backend image build, and dependency review. Code
scanning runs when a release is published, not on pull requests.

## Phases

The project is built in phases, each a contract written before its code: [`PLAN.md`](../../PLAN.md)
and `docs/plan/`. A phase is complete only when every item of the completion rule in `PLAN.md` holds,
and its verification report is appended to its phase file. [`verification.md`](verification.md)
says how to produce one.

## Releases

Releases are cut from `main` by a maintainer, as a tag `vMAJOR.MINOR.PATCH`.

1. Move the `## [Unreleased]` entries in `CHANGELOG.md` under a new `## [x.y.z] - YYYY-MM-DD`
   heading, and update the comparison links at the bottom. For 0.1.0 the section is written; add
   its date.
2. Set the same version in `apps/backend/pyproject.toml`, `apps/backend/src/letmehandle/__init__.py`
   and `apps/mobile/package.json`. The release workflow refuses a tag the backend's version does not
   match.
3. Merge that through a pull request.
4. Publish a GitHub release for tag `vx.y.z` on that commit.

Publishing the release runs `.github/workflows/release.yml`, which takes the notes from that version's
section of the changelog, builds the backend image and publishes it to the GitHub Container Registry
as `ghcr.io/<owner>/<repository>/backend:x.y.z` and `:x.y`, and runs code scanning. Pushing a `v*`
tag without a release does the same, and creates the release from the changelog. It refuses a tag
whose version the backend does not declare, and a version with no notes in the changelog. Nothing is
tagged `latest`: before 1.0, moving to a new minor version should be a choice.

## Versioning

[Semantic Versioning](https://semver.org/spec/v2.0.0.html), and before 1.0 it promises less:

- **`0.MINOR.0`** may change anything public — the HTTP API, configuration variables, the port
  interfaces providers implement, the database schema — and says so in the changelog under
  **Changed** or **Removed**, with what to do.
- **`0.MINOR.PATCH`** changes nothing public: fixes, documentation, dependency updates.
- Migrations only move forward from one release to the next. Upgrading runs
  `alembic upgrade head`; the image carries the migrations.
- Nothing before 1.0 is supported once the next minor version is out.

1.0 will be the first version that promises the HTTP API and the provider ports stay compatible
within a major version.
