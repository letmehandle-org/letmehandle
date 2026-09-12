# Phase 12 — Security, privacy and hardening

**Goal:** a dedicated adversarial pass over everything built, plus the documentation a
self-hoster needs to run it safely.

Security work happened in every prior phase. This phase is the review that assumes it was
insufficient.

## In scope

### Review, with a written finding for each area
- **Authentication** — token lifetime, rotation, revocation, reuse detection, OTP brute
  force, enumeration through timing or response shape, session fixation.
- **Authorisation** — every route and every repository method re-checked for user scoping.
  An automated test enumerates routes and fails on any that is unprotected without an
  explicit, justified exemption, so a route added later cannot quietly skip the check.
- **Agent authority** — the Phase 6 enforcement re-examined adversarially, with prompt
  injection delivered as caller speech. Caller content is data; the tests prove it cannot
  become instruction.
- **Secrets** — no secret in a tracked file, a log, an error response, a metric label, a
  crash report or a notification payload. History scanned, not just the working tree.
- **Personal data** — an inventory of every field, why it is held, how long, and how it is
  deleted. Account deletion implemented and proven to remove everything.
- **Transcripts** — encryption at rest verified by inspecting stored bytes; key handling
  documented; purge proven; retention bounds enforced server-side.
- **Logs** — no transcript content, no caller identity beyond what is necessary, no tokens,
  no numbers. A log scrubber with tests, plus an audit that fails the build on a log call
  that interpolates a sensitive field.
- **Provider credentials** — least privilege documented per provider; failure behaviour on
  revoked credentials.
- **Webhooks** — signature verification, replay windows, and rejection before parsing.
- **Rate limits** — on authentication, on webhooks, on all authenticated routes.
- **Input validation** — every boundary, including audio frames and provider payloads.
- **Dependencies** — vulnerability audit for both applications, with a policy for response.
  Two advisories are already open and were deliberately not fixed in phase 0; both are
  build-time only and neither reaches a running application:
  - `image-size`, reached through the JavaScript bundler. Two denial-of-service advisories
    with **no patched version published**. Exploiting either requires a hostile image asset
    to be inside the repository already, at which point the bundler is not the problem.
  - `decode-uri-component`, reached through the same toolchain. A patched version exists and
    was tried: it is ESM only, and overriding to it breaks the test runner. Recorded rather
    than forced.

  This phase decides whether they are accepted, pinned, or vendored around, and writes the
  decision down. They are not to be quietly inherited.
- **Mobile** — secure storage, certificate handling, no sensitive data in logs, screenshot
  and backup exclusion for sensitive screens, jailbreak or root posture stated.

### Deliverables
- `SECURITY.md` completed: supported versions, reporting through private advisories, and
  response expectations.
- `docs/architecture/security.md`: the threat model, the trust boundaries, and what the
  project does and does not defend against, stated plainly.
- `docs/development/self-hosting-security.md`: what an operator must do — credentials, key
  management, network exposure, retention configuration, and what is their responsibility
  rather than the project's.
- Automated checks added to `make verify` and CI: secret scanning over history, dependency
  audit, the route authorisation enumerator, and the log scrubber audit.

## Explicitly out of scope

- Formal penetration testing or external audit.
- Compliance certification. The privacy design is documented; a certification is a
  programme, not a phase.

## Tests required

| Kind | Must prove |
| --- | --- |
| Security | Every reviewed area has at least one test asserting the property holds. Not a checklist: a suite. |
| Automated | Route enumeration fails on an unprotected route; log audit fails on a sensitive interpolation; secret scan fails on a planted credential; each proven by deliberately introducing the fault. |
| Privacy | Account deletion removes every row and every derived artefact, proven by querying for residue. |
| Adversarial | Prompt injection delivered as caller speech does not cause an unauthorised tool execution. Several phrasings. |
| Data | Stored transcript bytes are not readable without the key. |

## Acceptance criteria

1. Every listed area is reviewed with a written finding and every finding is resolved or
   recorded as an accepted risk with a reason.
2. No secret appears in the working tree or in history.
3. No personal data or transcript content appears in any log, metric or error response.
4. Every route is authenticated and user-scoped, or explicitly and justifiably exempt.
5. Account deletion is complete and proven.
6. Transcript encryption is verified at the stored-bytes level.
7. Prompt injection through caller speech cannot produce an unauthorised action.
8. Dependency audits are clean or have documented, justified exceptions.
9. Security documentation exists and is accurate.
10. The new automated checks run in CI and fail on a planted fault.

## Risks and open questions

- **Findings that need architectural change.** Possible, and the reason this phase is before
  release rather than after. A finding that requires a change gets the change; the phase does
  not complete around it.
- **Self-hoster responsibility.** Much of the real risk sits with whoever deploys it. The
  documentation must be explicit about the split rather than implying the project handles it.
