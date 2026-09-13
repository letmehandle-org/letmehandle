## What this changes

<!-- One paragraph. What it does, and why it is needed. -->

## Phase

<!-- Which phase in PLAN.md this belongs to, and which planned task. If it belongs to no
     phase, say so and why it should land anyway. -->

## How it was verified

<!-- The commands you ran, and anything you checked by hand that automation cannot reach. -->

## Checklist

- [ ] `make verify` passes locally
- [ ] Tests cover the change, including its failure paths
- [ ] Coverage floors hold (98% backend, 90% mobile logic)
- [ ] No vendor-specific behaviour leaked into domain code
- [ ] No unfinished code: no stubs, no commented-out code, no silently swallowed exceptions
- [ ] Documentation affected by this change is updated, including a provider's page, the
      capability matrix, and `make config-reference` for a new or changed setting
- [ ] `make api-types` was run if a request or response changed
- [ ] A line under `## [Unreleased]` in `CHANGELOG.md`, if a user or contributor would notice
- [ ] No credential, account identifier, resource name or personal datum in the diff or the
      commit messages
- [ ] Commits are small, conventional, and each leaves the repository working
