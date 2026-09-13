# Design

The mobile app, screen by screen. Everything here exists to be read while building it.

| File | What it is |
|---|---|
| `screenshots/*.png` | what each screen looks like — start here |
| `screens/*.html` | the same screen as markup: structure, copy, and which tokens and icons it uses |
| `tokens.css` | every colour, type size, weight, spacing, radius and shared component |
| `icons.svg` | the icon set; screens reference icons as `#i-name` |

**Building a screen:** open its screenshot, read its HTML for structure and copy, and take
every value from `tokens.css`. Implement what is drawn. A state that is not drawn is raised,
not invented (phase 9).

## The system

| | |
|---|---|
| Layout | Light ground `#F8F6FF`; white cards `#FFFFFF`; one pastel hero card `#E9E3FF → #D8CFFB` carrying the ring |
| The ring | The app's central object: calls handled (violet), calls that needed the user (apricot), the rest |
| Assistant | Violet `#7C6BEA`; text in violet uses `#4F3FB8` |
| Needs the user | Apricot `#FBA36B`, text `#B5641D` — never used for anything else |
| Destructive | Red `#C2364E`, kept apart from the brand colour |
| Type | Poppins only. 300 for large figures, 400 for reading, 600 for titles and buttons. No tabular digits. |
| Icons | 1.5 px stroke. The assistant is always `i-bot`; `i-bubble-q` can't resolve; `i-siren` urgent; `i-phone-ring` ringing; `i-inbox` later. |
| Navigation | Three tabs: Home, Activity, Settings. Sub-pages have a round back button and a centred title. |

## Principles

- **Understood without reading.** Icons and layout carry meaning; copy is only what an icon cannot say.
- **Short onboarding.** Four steps. Anything optional is in settings with a safe default; a voice is pre-selected.
- **One rule, not a matrix.** Contacts ring the user; unknown numbers meet the assistant; known spam is turned away.
- **Absent, not disabled.** A capability the transport lacks has no control at all (`b07-home-screening-only`).
- **Nothing the product does not do.** No voice cloning, no in-call controls, no search, no dialling out, no pause switch.

## Reading a screen file

Each `screens/*.html` starts with a meta block. The HTML is only the screen's content;
the status bar and tab bar are implied by these fields.

```
title   the state
group   where it sits in the app
tab     which tab is active: home | activity | settings | none
badge   a tab showing the apricot dot
status  hide = no status bar (launch, lock screen)
class   scopes the screen's own <style> block
note    why the screen is the way it is
```

## Where the design meets the code

Settled in `docs/architecture/decisions.md`:

1. **Hours** — one window when the assistant answers, none meaning 24/7; outside it calls ring the user (D-027).
2. **Routing by contacts** — the address book never leaves the phone; the server knows only important contacts (D-028).
3. **Onboarding** — four steps: call handling, hours, authority, notifications (D-029).

Still open:

4. **Escalation threshold** — `escalate_at_or_above` has no control and keeps its default.

## Screens

### Getting started

| Screen | State |
|---|---|
| [`a01-splash`](screenshots/a01-splash.png) · [html](screens/a01-splash.html) | Launch |
| [`a02-welcome`](screenshots/a02-welcome.png) · [html](screens/a02-welcome.html) | Welcome |
| [`a03-phone`](screenshots/a03-phone.png) · [html](screens/a03-phone.html) | Your number |
| [`a04-code`](screenshots/a04-code.png) · [html](screens/a04-code.html) | Code sent |
| [`a05-code-error`](screenshots/a05-code-error.png) · [html](screens/a05-code-error.html) | Code rejected |
| [`a06-setup-who`](screenshots/a06-setup-who.png) · [html](screens/a06-setup-who.html) | Setup 1 — how your calls work |
| [`a07-setup-when`](screenshots/a07-setup-when.png) · [html](screens/a07-setup-when.html) | Setup 2 — when you're called |
| [`a08-setup-hours`](screenshots/a08-setup-hours.png) · [html](screens/a08-setup-hours.html) | Setup 3 — hours |
| [`a09-setup-authority`](screenshots/a09-setup-authority.png) · [html](screens/a09-setup-authority.html) | Setup 4 — what it may do |
| [`a10-setup-done`](screenshots/a10-setup-done.png) · [html](screens/a10-setup-done.html) | Ready |

### Home

| Screen | State |
|---|---|
| [`b01-home`](screenshots/b01-home.png) · [html](screens/b01-home.html) | Home — at rest |
| [`b02-home-needs-you`](screenshots/b02-home-needs-you.png) · [html](screens/b02-home-needs-you.html) | Home — it needs you |
| [`b03-home-not-on-duty`](screenshots/b03-home-not-on-duty.png) · [html](screens/b03-home-not-on-duty.html) | Home — not on duty |
| [`b04-home-first-day`](screenshots/b04-home-first-day.png) · [html](screens/b04-home-first-day.html) | Home — day one |
| [`b05-home-offline`](screenshots/b05-home-offline.png) · [html](screens/b05-home-offline.html) | Home — offline |
| [`b06-home-loading`](screenshots/b06-home-loading.png) · [html](screens/b06-home-loading.html) | Home — loading |
| [`b07-home-screening-only`](screenshots/b07-home-screening-only.png) · [html](screens/b07-home-screening-only.html) | Home — screening phone |

### When it needs you

| Screen | State |
|---|---|
| [`c01-lock-notification`](screenshots/c01-lock-notification.png) · [html](screens/c01-lock-notification.html) | Lock screen |
| [`c02-escalation`](screenshots/c02-escalation.png) · [html](screens/c02-escalation.html) | It needs you — now |
| [`c03-escalation-later`](screenshots/c03-escalation-later.png) · [html](screens/c03-escalation-later.html) | It needs you — no rush |
| [`c04-escalation-missed`](screenshots/c04-escalation-missed.png) · [html](screens/c04-escalation-missed.html) | You missed it |
| [`c05-escalation-ended`](screenshots/c05-escalation-ended.png) · [html](screens/c05-escalation-ended.html) | Opened too late |

### Activity and summaries

| Screen | State |
|---|---|
| [`d01-activity`](screenshots/d01-activity.png) · [html](screens/d01-activity.html) | Activity |
| [`d02-activity-filter`](screenshots/d02-activity-filter.png) · [html](screens/d02-activity-filter.html) | Activity — filters |
| [`d03-activity-empty`](screenshots/d03-activity-empty.png) · [html](screens/d03-activity-empty.html) | Activity — nothing yet |
| [`d04-summary-resolved`](screenshots/d04-summary-resolved.png) · [html](screens/d04-summary-resolved.html) | Summary — settled by itself |
| [`d05-summary-you-joined`](screenshots/d05-summary-you-joined.png) · [html](screens/d05-summary-you-joined.html) | Summary — you joined |
| [`d06-summary-refused`](screenshots/d06-summary-refused.png) · [html](screens/d06-summary-refused.html) | Summary — refused |
| [`d07-summary-failed`](screenshots/d07-summary-failed.png) · [html](screens/d07-summary-failed.html) | Summary — it went wrong |
| [`d08-transcript`](screenshots/d08-transcript.png) · [html](screens/d08-transcript.html) | What was said |
| [`d09-transcript-gone`](screenshots/d09-transcript-gone.png) · [html](screens/d09-transcript-gone.html) | Words already deleted |
| [`d10-delete-call`](screenshots/d10-delete-call.png) · [html](screens/d10-delete-call.html) | Delete this call |

### Settings

| Screen | State |
|---|---|
| [`e01-settings`](screenshots/e01-settings.png) · [html](screens/e01-settings.html) | Settings |
| [`e02-who-gets-through`](screenshots/e02-who-gets-through.png) · [html](screens/e02-who-gets-through.html) | Who gets through |
| [`e03-when-youre-called`](screenshots/e03-when-youre-called.png) · [html](screens/e03-when-youre-called.html) | When you're called |
| [`e04-hours-24-7`](screenshots/e04-hours-24-7.png) · [html](screens/e04-hours-24-7.html) | Hours — around the clock |
| [`e05-hours-set`](screenshots/e05-hours-set.png) · [html](screens/e05-hours-set.html) | Hours — a set window |
| [`e06-authority`](screenshots/e06-authority.png) · [html](screens/e06-authority.html) | What it may do |
| [`e07-personalise`](screenshots/e07-personalise.png) · [html](screens/e07-personalise.html) | Personalise |
| [`e08-topics`](screenshots/e08-topics.png) · [html](screens/e08-topics.html) | Topics |
| [`e09-disclosable`](screenshots/e09-disclosable.png) · [html](screens/e09-disclosable.html) | What it may say about you |
| [`e10-privacy`](screenshots/e10-privacy.png) · [html](screens/e10-privacy.html) | Privacy |
| [`e11-account`](screenshots/e11-account.png) · [html](screens/e11-account.html) | Account |

### When things go wrong

| Screen | State |
|---|---|
| [`f01-save-failed`](screenshots/f01-save-failed.png) · [html](screens/f01-save-failed.html) | A change did not save |
| [`f02-offline-queued`](screenshots/f02-offline-queued.png) · [html](screens/f02-offline-queued.html) | Waiting for a connection |
| [`f03-notifications-denied`](screenshots/f03-notifications-denied.png) · [html](screens/f03-notifications-denied.html) | Notifications are off |
