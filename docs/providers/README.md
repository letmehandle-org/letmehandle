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

| Port | Interface | Contract suite | First implementation |
| --- | --- | --- | --- |
| `CallTransport` | `ports/call_transport.py` | `tests/contracts/call_transport.py` | phase 7, two of them, [streaming documented](call-transport.md) |
| `SpeechProvider` | `ports/speech.py` | `tests/contracts/speech.py` | phase 5, [documented](speech.md) |
| `LLMProvider` | `ports/llm.py` | `tests/contracts/other_ports.py` | phase 6 |
| `VoiceProvider` | `ports/voice.py` | `tests/contracts/other_ports.py` | phase 4, [documented](voice.md) |
| `NotificationProvider` | `ports/notification.py` | `tests/contracts/other_ports.py` | phase 10 |
| `OTPProvider` | `ports/otp.py` | `tests/contracts/other_ports.py` | phase 2 |
| `Clock`, `IdGenerator` | `ports/clock.py` | `tests/contracts/other_ports.py` | phase 2 |

Interface paths are relative to `apps/backend/src/letmehandle/domain/`, suites to `apps/backend/`.

## Capabilities, by transport

Transports differ in kind, not only in supplier, which is why the core asks what a transport can
do rather than which one it is. The flags are `TransportCapabilities` in
`ports/call_transport.py`, and a transport declares only what it genuinely provides — the full
matrix is written up in phase 15.

An operation that depends on a capability is not on `CallTransport` itself. It is reached by
narrowing — `answering(transport)`, `screening(transport)`, `audio_streaming(transport)`,
`bridging(transport)`, `three_way(transport)` — so a caller that has not checked cannot name the
method, and a transport whose declaration and implementation disagree fails at the narrowing
rather than in the middle of somebody's call.

## Offering one

Open a provider support issue first if the interface does not fit what you need. Otherwise
implement it, add a page here, and open a pull request.
