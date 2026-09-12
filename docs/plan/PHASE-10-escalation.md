# Phase 10 — Human escalation experience

**Goal:** when the phone rings because the assistant needs the user, the user already knows
why before answering.

## In scope

### Delivery
- `NotificationProvider` implemented twice: direct APNs and direct FCM (D-015), each behind
  the same port, selected by device platform.
- Device registration: token stored against the user's device row from Phase 2, refreshed on
  rotation, removed on sign-out and on rejection by the platform.
- Time-critical delivery priority, and a payload small enough to survive platform limits.

### Two escalation paths, one set of domain events

What escalation means physically depends on the transport's capabilities. What it means to the
product does not: the same domain events and the same outcomes are recorded either way, so
history, summaries and metrics are transport-independent.

**Where the transport can bridge** (`can_bridge_human`): the user's phone is dialled, the
caller stays connected throughout, and answering joins the human to the call already in
progress. This is the full experience and the one the notification was designed for.

**Where it cannot** (the native screening path): no bridge is fabricated. The escalation is the
handoff the platform genuinely supports — the call is allowed through to ring natively, with
the notification carrying whatever context was established before it rang. The product does not
pretend a caller is being held on a line that no longer exists.

The orchestrator selects between these by capability (phase 8). Neither path is named in the
notification layer, which receives an escalation and its context and nothing else.

### Payload
Enough for the user to answer knowing what they are walking into, and no more:

- Why the assistant escalated, in the words a person would use.
- Who is calling, as far as it is known.
- What the assistant has established so far.
- What the assistant believes is needed from the user.
- The call identifier, so the app can bind the notification to the call.

The payload carries no transcript, no recording reference, and no more personal data than the
screen will show.

### Ordering and failure
D-016 is the governing rule: the ring is authoritative, the notification supplements it.

- The notification may arrive before the ring, after the ring, or after the call has already
  ended. All three are designed states.
- A notification for a call that is over resolves to a summary rather than a live context.
- Duplicate deliveries are deduplicated by call identifier.
- Delivery failure never blocks, delays, or cancels the escalation. It is recorded as a
  degraded outcome and surfaced in the app's activity instead.
- If the app is opened without ever receiving the notification, the escalation context is
  fetched from the backend, so push is an accelerator and never the only path.

### In-app
- An escalation context screen, reachable from the notification and from a cold start.
- A live indicator on Home while an escalation is in progress.
- Permission request at the right moment with an explanation, and a graceful path for a user
  who declines: the product still works, and the app says what they will miss.

## Explicitly out of scope

- Answering or controlling the call from within the app. The call is a normal phone call.
- Rich or actionable notifications beyond what the platforms support plainly.
- Notification types other than escalation.

## Tests required

| Kind | Must prove |
| --- | --- |
| Unit | Payload construction and size limits; token lifecycle including rotation and rejection; deduplication by call id; failure recorded and never propagated to the escalation path. |
| Integration | Escalation dispatches to the correct provider per platform; a failing provider does not affect the call; the context endpoint returns the same information the payload carried. |
| Ordering | Notification before ring, after ring, and after call end each produce the correct app state. Three named tests. |
| Capability | Escalation on a bridging transport and on a non-bridging one both produce the same domain events, and only the bridging path attempts a bridge. |
| Mobile | Cold start from a notification; foreground receipt; permission denied; context fetched when no notification arrived. |
| E2E | Escalation fires, the handset rings, the notification arrives with correct context, the user answers and joins the live call. |
| Manual | Verified on a physical iPhone and a physical Android device, recorded in the report. |

## Acceptance criteria

1. An escalation sends a notification to the correct platform provider.
2. The notification carries context that explains the call in one reading.
3. Notification arrival before, after, or long after the ring is handled correctly.
4. Escalation produces the same domain events and outcomes on both paths.
5. No path fabricates a bridge the transport cannot perform.
4. Delivery failure never affects the escalation itself.
5. Escalation context is retrievable from the backend without a notification.
6. Duplicates are deduplicated.
7. Token lifecycle is handled including rotation, sign-out and rejection.
8. A user who denies notification permission retains a working product.
9. Verified on physical devices on both platforms.
10. Coverage meets the D-020 floors.

## Risks and open questions

- **Push latency against a ringing phone.** Push is not real-time and the handset may ring
  first. That is why D-016 makes the ring authoritative; the app is designed for the
  notification to be late.
- **Credentials for two platforms.** APNs keys and FCM credentials are environment
  configuration only, never tracked (D-021), and both are documented as named variables.
- **Device testing.** Physical devices are required for the manual criteria; simulators do
  not reproduce push reliably.
