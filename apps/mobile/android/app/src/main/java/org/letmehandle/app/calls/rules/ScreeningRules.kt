package org.letmehandle.app.calls.rules

import java.time.Instant

/** What was done with a call before it rang. Mirrors `ScreeningDecision`. */
enum class ScreeningDecision(val wire: String) {
  ALLOW("allow"),
  REJECT("reject"),
  SILENCE("silence"),
}

/** Why, for the log line a screening leaves. Never contains the caller's number. */
enum class ScreeningReason {
  NO_RULES,
  STALE_RULES,
  WITHHELD_NUMBER,
  IMPORTANT_CONTACT,
  BLOCKED_CATEGORY,
  CATEGORY_POSTURE,
  DEFAULT_POSTURE,
  QUIET_HOURS,
  TIMED_OUT,
  FAILED,
}

data class Screening(val decision: ScreeningDecision, val reason: ScreeningReason)

/**
 * Who is calling, as far as a screening service is told.
 *
 * `withheld` is the caller's choice not to present a number. The platform does not normally pass
 * such calls to a screening service at all; the case is still decided rather than assumed away.
 * `number` is null when withheld, and also when a number was presented in a form that cannot be
 * read — which is not the same thing, and is not treated as anonymous.
 */
data class ScreenedCaller(val number: CallerNumber?, val withheld: Boolean) {
  init {
    require(!withheld || number == null) { "a withheld number has no number" }
  }
}

/**
 * The user's deterministic call rules, applied to one call.
 *
 * Pure: no clock, no storage, no platform. Everything it needs is passed in, which is what lets
 * every rule be tested on the JVM and what keeps it fast enough to never approach the deadline.
 *
 * The order, and the reasons for it:
 *
 *   1. No snapshot, or a stale one (older than [CallRulesSnapshot.MAX_AGE], or dated further in
 *      the future than the clock-skew allowance): the call rings. A caller is never refused on
 *      rules the handset does not have or can no longer vouch for.
 *   2. A withheld number: the anonymous posture.
 *   3. An important contact: their own posture. A contact the user asked to put through rings
 *      during quiet hours too — that is what naming them was for.
 *   4. Everybody else is an unknown caller. The handset does not classify callers, so the only
 *      category it can honestly apply is `unknown`: blocked, then its posture, then the default.
 *
 * Postures become decisions like this. `reject` rejects. `pass_through` rings. `handle_with_agent`
 * also rings, because this path has no assistant to hand the call to, and hiding a call the user
 * did not ask to hide is worse than letting it ring. Either of those is silenced in quiet hours.
 */
object ScreeningRules {
  fun evaluate(snapshot: CallRulesSnapshot?, caller: ScreenedCaller, now: Instant): Screening {
    if (snapshot == null) {
      return Screening(ScreeningDecision.ALLOW, ScreeningReason.NO_RULES)
    }
    if (snapshot.isStaleAt(now)) {
      return Screening(ScreeningDecision.ALLOW, ScreeningReason.STALE_RULES)
    }

    if (caller.withheld) {
      return decide(snapshot, snapshot.anonymousPosture, ScreeningReason.WITHHELD_NUMBER, now)
    }

    val contact =
        caller.number?.let { presented ->
          snapshot.importantContacts.firstOrNull { presented.matches(it.phoneNumber) }
        }
    if (contact != null) {
      return if (contact.posture == HandlingPosture.PASS_THROUGH) {
        Screening(ScreeningDecision.ALLOW, ScreeningReason.IMPORTANT_CONTACT)
      } else {
        decide(snapshot, contact.posture, ScreeningReason.IMPORTANT_CONTACT, now)
      }
    }

    if (CallerCategory.UNKNOWN in snapshot.blockedCategories) {
      return Screening(ScreeningDecision.REJECT, ScreeningReason.BLOCKED_CATEGORY)
    }
    val categoryPosture = snapshot.postureByCategory[CallerCategory.UNKNOWN]
    return if (categoryPosture != null) {
      decide(snapshot, categoryPosture, ScreeningReason.CATEGORY_POSTURE, now)
    } else {
      decide(snapshot, snapshot.defaultPosture, ScreeningReason.DEFAULT_POSTURE, now)
    }
  }

  private fun decide(
      snapshot: CallRulesSnapshot,
      posture: HandlingPosture,
      reason: ScreeningReason,
      now: Instant,
  ): Screening =
      when {
        posture == HandlingPosture.REJECT -> Screening(ScreeningDecision.REJECT, reason)
        snapshot.quietHours?.contains(now) == true ->
            Screening(ScreeningDecision.SILENCE, ScreeningReason.QUIET_HOURS)
        else -> Screening(ScreeningDecision.ALLOW, reason)
      }
}
