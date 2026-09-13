# NotificationProvider

Context for the ring. When the assistant needs the user, their phone rings through the call
transport, and a push notification says why. The ring is authoritative (D-016): a notification
may arrive before it, after it, after the call has ended, twice, or never, and none of those
changes what happens to the call.

Interface: `apps/backend/src/letmehandle/domain/ports/notification.py`
Contract suite: `NotificationProviderContract` in `apps/backend/tests/contracts/other_ports.py`
Implementations: `adapters/notification/apns/` (iOS) and `adapters/notification/fcm/` (Android),
each talking to its platform directly with no vendor SDK (D-015).

## What the port asks

| Member | Means |
| --- | --- |
| `platform` | Which devices it reaches. One provider per platform; the dispatcher routes by it |
| `payload_limit_bytes` | The largest payload the platform accepts |
| `payload_size(notification)` | How many bytes this provider would send, measured on its own encoding |
| `send(token, notification)` | One delivery, reported as an outcome and never raised |

`send` returns `DELIVERED`, `REJECTED` (will fail the same way until something is fixed),
`FAILED` (transient; the same request may succeed later) or `TOKEN_INVALID` (the device token is
dead and is removed). It raises only for a defect — a token for the other platform.

## Payload

Built once, provider-independently, in `application/escalation/notification.py`: why the
assistant escalated, who is calling as far as it knows, what it needs from the user, what it has
established, and the call id. No transcript, no recording reference, no caller number.

When a payload would exceed a platform's limit, the text is cut rather than the delivery failed:
the longest prefix that fits, in a fixed order — what has been established, then what is needed,
then the caller label, and the reason last. The context endpoint returns the same words untrimmed.

## iOS — APNs

Token-based authentication over HTTP/2 (`api.push.apple.com`, or `api.sandbox.push.apple.com`).

- **Provider token**: a JWT, ES256, `kid` = key id, `iss` = team id, `iat`. Reused until 50
  minutes old; a token APNs calls stale is replaced only once it is at least 20 minutes old,
  because replacing more often is itself an error.
- **Request**: `POST /3/device/<token>` with `apns-push-type: alert`, `apns-priority: 10`,
  `apns-expiration` ten minutes out, `apns-topic` = bundle id, `apns-collapse-id` = the call id
  (or its SHA-256 when longer than 64 bytes). Payload limit 4096 bytes.
- **Body**: `aps.alert` with title, subtitle (the caller label) and body; `sound`; and
  `interruption-level: time-sensitive`, which lets it through a Focus on iOS 15 and later when the
  app has the Time Sensitive Notifications capability.

| Response | Outcome |
| --- | --- |
| 200 | `DELIVERED` |
| 410 (`Unregistered`, `ExpiredToken`), 400 `BadDeviceToken` | `TOKEN_INVALID` |
| 400 `IdleTimeout`, 403 `ExpiredProviderToken`, 429 (`TooManyRequests`, `TooManyProviderTokenUpdates`), 500, 503 | `FAILED` |
| every other 4xx, including `DeviceTokenNotForTopic`, `BadTopic`, `InvalidProviderToken`, `PayloadTooLarge` | `REJECTED` |

`BadDeviceToken` is also what a token from the other environment gets, which is why
`APNS_ENVIRONMENT` has no default: a production deployment pointed at the sandbox would remove
every device.

## Android — FCM HTTP v1

- **Access token**: a JWT assertion (RS256, `iss` = service account email, `scope` =
  `https://www.googleapis.com/auth/firebase.messaging`, `aud` = token URI, one-hour `exp`)
  exchanged at the token URI. Cached until five minutes before `expires_in`; one refresh runs at a
  time; a 401 from FCM drops it so the next send obtains another.
- **Request**: `POST https://fcm.googleapis.com/v1/projects/<project>/messages:send` with a
  `notification` (title; body led by the caller label), `data` (identifiers only), and `android`:
  `priority: HIGH`, `ttl: 600s`, `collapse_key` and `notification.tag` = the call, and
  `notification.channel_id: escalation`. The app creates that channel with high importance; on
  Android 8 and later the channel decides how the notification interrupts. Payload limit 4096
  bytes.

| Error (`FcmError.errorCode`, else `status`) | Outcome |
| --- | --- |
| `UNREGISTERED` | `TOKEN_INVALID` |
| `INVALID_ARGUMENT` naming `message.token` in its field violations | `TOKEN_INVALID` |
| `INVALID_ARGUMENT` about anything else, `SENDER_ID_MISMATCH`, `THIRD_PARTY_AUTH_ERROR`, other 4xx | `REJECTED` |
| `UNAVAILABLE`, `INTERNAL`, `QUOTA_EXCEEDED`, `UNAUTHENTICATED`, 429, 5xx | `FAILED` |

## Configuration

All optional; see `.env.example`. Setting any variable of a platform requires the rest of that
platform, and an unreadable key stops startup without being repeated in the message. Keys are
given as content, not paths.

| Variable | For |
| --- | --- |
| `APNS_KEY_ID`, `APNS_TEAM_ID` | The signing key's identifiers |
| `APNS_PRIVATE_KEY` | The `.p8` key's content (secret) |
| `APNS_TOPIC` | The app's bundle id |
| `APNS_ENVIRONMENT` | `sandbox` or `production` |
| `FCM_PROJECT_ID` | The Firebase project |
| `FCM_SERVICE_ACCOUNT_JSON` | The service account key (secret) |

A device on a platform with no provider is recorded as `not_configured` at dispatch; nothing fails.

## Dispatch and the rest of the lifecycle

`EscalationDispatcher` (`application/escalation/dispatch.py`) claims the escalation's context in
storage — the first claim per user and call wins, which is the deduplication — then sends to every
registered device concurrently under a five-second deadline, removes tokens reported dead, and
records `delivered`, `failed` or `no_devices` on the context. It never raises. Counts go to
`escalation.dispatch`, `escalation.delivery` and `escalation.token_removed`, labelled by outcome,
platform and provider only.

Devices register with `PUT /v1/devices` (idempotent; `previous_token` removes a rotated one),
leave with `POST /v1/devices/unregister` or by naming the device on sign-out, and the app reads an
escalation with `GET /v1/escalations/{call_id}` when no notification arrived.
