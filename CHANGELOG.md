# Changelog

Notable changes, in the format of [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioned per [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Pre-1.0: the public interface may change in a minor version. What that promises is stated at
the first release.

## [Unreleased]

### Added
- Build plan covering phases 0 to 15, with per-phase scope, tests and acceptance criteria.
- Architecture decision record.
- Disclosure and secret audits, wired into commit, push and CI.
- Backend: FastAPI application with typed configuration validated at startup, structured
  logging with a request correlation id, health and readiness endpoints, and enforced
  architectural boundaries.
- Mobile: React Native application shell with typed navigation, a design-token layer, and
  translation wired from the first screen.
- Local development stack, backend image, and CI covering lint, types, tests, coverage, the
  backend image, and native builds for both platforms.
