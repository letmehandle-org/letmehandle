# Manual verification

What the automated suite cannot reach, done by hand before a release and recorded in the phase's
verification report. The automated scenarios run against a simulated telephony provider, a simulated
speech service, a scripted model and recording push providers (`make e2e`); this script is the same
product against the real ones. [`live-rehearsal.md`](live-rehearsal.md) runs whole calls on the
real speech service and model with only the telephone network simulated, and is worth running
first.

Nothing in this document, or in the report it produces, carries a real phone number, host, account
identifier, token or device token. Write them as `<user line>`, `<caller handset>`, `<base url>` and
so on, and keep the real values where the deployment keeps its secrets.

## What is needed

- A deployment reachable at a public HTTPS URL, `<base url>`: a staging host, or the development
  stack behind a tunnel. `TELEPHONY_WEBHOOK_BASE_URL` is exactly that URL.
- A telephony account with one voice number, configured as `docs/providers/call-transport.md`
  describes, and the deployment's `TELEPHONY_*` variables set for it.
- A speech service and a model endpoint the deployment is configured for (`SPEECH_*`, `LLM_*`).
- Push credentials for both platforms (`APNS_*` with `APNS_ENVIRONMENT=sandbox` for a development
  build, `FCM_*`), as `docs/providers/notifications.md` describes.
- Three phones:
  - **the user's iPhone**, with a development build signed in;
  - **the user's Android phone**, with a development build signed in, able to hold the call
    screening role (Android 10 or later);
  - **a caller handset**, any phone, whose number is not one of the user's important contacts.
- The user's own line set to forward unanswered and busy calls to the deployment's number. Which of
  the user's two phones holds that line depends on the SIM; run section 2 from whichever does, and
  section 4 on the Android phone.
- A terminal with the deployment's logs.

Before starting: `GET <base url>/health` answers, the startup log line lists both notification
providers, and signing in on each phone succeeds with a code received by text message.

## 1. Push, on each platform

For each of the two phones:

1. Open the app, sign in, and allow notifications when asked.
2. In the logs, confirm a device registration for the platform, and no `escalation.delivery_errored`.
3. Place the call in section 3 with this phone as the only registered device (sign the other one
   out), and confirm the notification arrives on it while the phone rings, showing a reason, who is
   calling as far as is known, and what was established — and no number, no transcript.
4. Lock the phone and repeat: the notification appears on the lock screen.
5. With notifications turned off for the app in the phone's settings, repeat: the phone still rings
   for the escalation, and opening the app shows the same context (`GET /v1/escalations/<call>`).

Record for each platform: delivered yes/no, seconds between the ring starting and the notification
appearing, and what the lock screen showed.

## 2. A real inbound call the assistant handles

1. In the app, set call handling so unknown callers go to the assistant, no hours (around the clock),
   and grant nothing.
2. From the caller handset, ring the user's line and let it forward.
3. Confirm: the caller hears the assistant within two seconds of being answered; the assistant hears
   and answers ordinary speech; talking over it stops it promptly.
4. Say you are confirming an appointment, and hang up.
5. In the app, the call appears in history, ended, with a headline, the outcome, and a transcript that
   matches what was said.
6. In the logs: one `call.ended`, no `call.run_failed`, no `call.provider_failed`, and no line that
   carries the caller's number unmasked or anything that was said.

Record: time from ringing to the assistant's first word, whether barge-in worked, the stored outcome,
and whether the carrier sent `ForwardedFrom` (D-033) — without its value.

## 3. A real escalation to a real handset

1. Keep the settings from section 2, with both phones registered for push.
2. From the caller handset, ring the user's line; when the assistant answers, ask to speak to the
   user in person because it is urgent.
3. Confirm the user's phone rings within a few seconds, and the notification arrives (section 1).
4. Answer on the user's phone. Confirm all three can hear each other: the caller never heard a
   transfer or a redial, the user hears the caller, and the assistant behaves as the presence the
   policy chose.
5. The caller hangs up. Confirm the user's phone is released too, and nothing rings afterwards.
6. In history: outcome handed to the user, a human-joined time between the escalation and the end.
7. Repeat, and this time do not answer on the user's phone. Confirm the ring stops, the assistant
   tells the caller the user is unavailable and carries on, and history records the unanswered
   escalation.
8. Repeat, and hang up the caller handset while the user's phone is still ringing. Confirm the
   user's phone stops ringing within a few seconds and the app shows the escalation as ended.

Record, for each of 4, 7 and 8: seconds from the request to the ring, what each party heard, the
stored outcome. Record also the conference facts D-027 leaves to the first real call: what a
participant's inbound audio contains, whether a held participant hears anything, and the frame size.

## 4. Native screening on Android

1. On the Android phone, turn screening on and grant the call screening role.
2. **Allowed.** With unknown callers set to ring, call from the caller handset: the phone rings, and
   after the call the activity shows it as put through.
3. **Rejected.** Block unknown callers, call again: the phone does not ring, the caller is refused,
   and the activity shows the rejection and why.
4. **Silenced.** Today's rules never silence a call; record that no setting produces one.
5. **Under the deadline.** With the phone in battery saver and the app killed, call again: the
   decision is still applied, or the call rings; it is never left undecided. Record how long the
   decision took from the screening log line.
6. **Role withdrawn.** While the app is running, remove the call screening role in the phone's
   settings. Return to the app: it says screening is off and why, and the next call rings normally.
   Grant the role again and confirm screening resumes.
7. Put the phone in flight mode, take a call as it comes back, and confirm the reports reach the
   backend once the phone is online and nothing is recorded twice.

Record, for each: what the phone did, what the activity showed, and the time the decision took.

## Recording the run

Add a `Manual verification` line to the phase's verification report
(`docs/plan/VERIFICATION-TEMPLATE.md`) naming each section, the build and deployment versions used,
the phones' operating system versions, and each result above. A step that could not be done is
written down with why, not left out.
