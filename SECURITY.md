# Security policy

## Reporting a vulnerability

Report privately through GitHub's security advisories:

**https://github.com/letmehandle-org/letmehandle/security/advisories/new**

Please do not open a public issue for a vulnerability, and please do not disclose it publicly
until a fix is available.

There is no email address here on purpose. Private advisories keep the report, the
discussion, and the fix in one place, and they do not depend on anyone watching an inbox.

### What helps

- What an attacker can do, and what they need in order to do it.
- The steps to reproduce it.
- The commit or version you tested.
- Anything you think mitigates it.

### What to expect

This is a small project. An acknowledgement within a week, an assessment within two, and a
fix timed to the severity. You will be credited unless you would rather not be.

## Supported versions

Pre-1.0. Only the latest tag is supported. This will be replaced with a real support matrix
at the first stable release.

## Scope

In scope: this repository, and any deployment built from it using the documented
configuration.

Out of scope: vulnerabilities in third-party providers, which should go to that provider; and
misconfiguration of a self-hosted deployment, though if our documentation led you there, that
is a documentation bug worth reporting.

## Running this safely

This software answers phone calls, holds conversations, and acts on someone's behalf. If you
deploy it, several things are yours rather than ours: credential storage, key management,
network exposure, and retention configuration. The split is written out in
`docs/development/self-hosting-security.md`, which lands in phase 12.
