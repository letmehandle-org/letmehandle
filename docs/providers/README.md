# Providers

Every external capability is a port. This directory documents each one and how to implement
it.

## The contract

An implementation must:

1. Implement the port interface, with no extra public surface. If callers need something the
   interface does not offer, the interface changes — for everyone — rather than the caller
   reaching around it.
2. Declare its capabilities honestly. The product renders and routes from these, so
   over-declaring produces a feature that fails in front of a caller.
3. Pass the port's contract suite in `apps/backend/tests/contracts/`, unmodified. If the suite
   is wrong, fix the suite for every implementation, not just yours.
4. Keep every vendor type inside the adapter. No SDK object crosses the port boundary.
5. Convert at its own edge. Audio encodings, sample rates, identifiers and error shapes are
   translated in the adapter, so the core sees domain types only.
6. Map failures to the domain's error taxonomy. A caller must not have to catch a vendor
   exception.

## Capabilities, not assumptions

A port that cannot express what a provider does is a port with a design problem. The answer is
a new capability, never a branch in core code on which provider is configured.

When a capability is absent, the product does not present the feature. It is not disabled, not
greyed out, not labelled as coming soon — it is not there. A user should never be shown
something their configuration cannot do.

## The ports

Each gets a page here as it is implemented. Until then the interface itself, in
`apps/backend/src/letmehandle/domain/ports/`, is the specification.

| Port | Page | Status |
| --- | --- | --- |
| `SpeechProvider` | — | interface in phase 1, first adapter in phase 5 |
| `LLMProvider` | — | interface in phase 1, first adapter in phase 6 |
| `CallTransport` | — | interface in phase 1, two adapters in phase 7 |
| `VoiceProvider` | — | interface in phase 1, first adapter in phase 4 |
| `NotificationProvider` | — | interface in phase 1, first adapters in phase 10 |
| `OTPProvider` | — | interface in phase 1, mock adapter in phase 2 |

## Offering one

Open a provider support issue first if the interface does not fit what you need. Otherwise
implement it, add a page here, and open a pull request.
