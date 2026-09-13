# Security policy

## Reporting a vulnerability

Report privately through GitHub's security advisories:

**https://github.com/letmehandle-org/letmehandle/security/advisories/new**

That is the only channel. Please do not open a public issue, pull request or discussion for a
vulnerability, and please do not disclose it publicly until a fix is available or we have agreed
a date together.

There is no email address here on purpose. Private advisories keep the report, the discussion,
and the fix in one place, and they do not depend on anyone watching an inbox.

### What to include

- What an attacker can do, and what they need in order to do it: network position, an account,
  a phone line, access to a provider's console.
- The steps to reproduce it, as small as you can make them.
- The commit you tested, and the configuration that matters: `APP_ENV`, `TELEPHONY_PROVIDER`,
  and whether transcript keys were set. Never paste a real key, token or phone number; use
  placeholders such as `+15555550100`.
- What you think it affects: which data, which accounts, which calls.
- Anything you think mitigates it.

A report does not need a fix or a polished write-up. A clear description of the behaviour is
enough to start.

### What to expect

This is a small project, so these are intentions rather than guarantees:

- An acknowledgement within a week.
- An assessment — whether we can reproduce it, and how severe we think it is — within two.
- A fix timed to the severity, developed in a private fork attached to the advisory, and
  published with the advisory once it is released.

You will be credited in the advisory unless you would rather not be. If we disagree about
severity or scope, we will say why in the advisory thread.

## Supported versions

Releases are named for their date (`vYYYY.M.D`). Security fixes go to the default branch and into
the next release; the most recent release is the only supported one, and there are no backports to
earlier ones.

| Version | Supported |
| --- | --- |
| Latest release, and the default branch | Yes |
| Anything older | No |

## Scope

In scope:

- The backend, the mobile app and the scripts in this repository.
- A deployment built from it using the documented configuration, including the threats and
  controls described in [`docs/architecture/security.md`](docs/architecture/security.md).
- Documentation that would lead somebody following it into an unsafe deployment. That is a
  documentation bug worth reporting.

Out of scope:

- Vulnerabilities in third-party services this project talks to — telephony, speech, model and
  push providers. Report those to the provider.
- A deployment that departs from
  [`docs/development/self-hosting-security.md`](docs/development/self-hosting-security.md): for
  example one reachable from a network with `APP_ENV` not set to `production`, or one whose
  database or keys have been exposed.
- Residual risks already written down in `docs/architecture/security.md`, unless you have found
  a way to make one worse than it says.
- Denial of service that needs volumetric traffic, and findings from automated scanners with no
  demonstrated impact.
- A compromised, rooted or jailbroken handset.

## Running this safely

This software answers phone calls, holds conversations, and acts on someone's behalf. If you
deploy it, several things are yours rather than the project's: credential storage, key
management, network exposure, and retention scheduling. What that means in practice is in
[`docs/development/self-hosting-security.md`](docs/development/self-hosting-security.md), and
what the project does and does not defend against is in
[`docs/architecture/security.md`](docs/architecture/security.md).
