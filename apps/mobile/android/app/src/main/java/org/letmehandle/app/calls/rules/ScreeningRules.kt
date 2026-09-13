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
  NUMBER_NOT_DELIVERED,
  IMPORTANT_CONTACT,
  BLOCKED_CATEGORY,
  CATEGORY_POSTURE,
  DEFAULT_POSTURE,
  TIMED_OUT,
  FAILED,
}

data class Screening(val decision: ScreeningDecision, val reason: ScreeningReason)

/**
 * Who is calling, as far as a screening service is told.
 *
 * The platform does not normally pass a call without a presented number to a screening service
 * at all; those cases are still decided rather than assumed away.
 */
sealed interface ScreenedCaller {
  /**
   * A number was presented. [number] is null when it was in a form that cannot be read — which
   * is not the same as having none, and is not treated as anonymous.
   */
  data class Presented(val number: CallerNumber?) : ScreenedCaller

  /** The caller chose not to present their number. */
  data object Withheld : ScreenedCaller

  /**
   * No number arrived, and not by the caller's choice: the network did not deliver one, or the
   * call is from a payphone. Nobody decided to be anonymous, so the anonymous posture is not
   * theirs to be refused by.
   */
  data object NotDelivered : ScreenedCaller
}

/**
 * The user's deterministic call rules, applied to one call.
 *
 * Pure: no clock, no storage, no platform. Everything it needs is passed in — the time, and the
 * country the handset is dialling in — which is what lets every rule be tested on the JVM and what
 * keeps it fast enough to never approach the deadline.
 *
 * The order, and the reasons for it:
 *
 *   1. No snapshot, or a stale one (older than [CallRulesSnapshot.MAX_AGE], or dated further in
 *      the future than the clock-skew allowance): the call rings. A caller is never refused on
 *      rules the handset does not have or can no longer vouch for.
 *   2. A withheld number: the anonymous posture. A number that simply did not arrive rings, as a
 *      caller put through would — never rejected.
 *   3. An important contact: their own posture. A contact is recognised by the
 *      caller's number in international form. When a national number cannot be put in that form,
 *      trailing digits that agree are only a guess, and a guess may let a call ring but never
 *      refuse or hide one: it counts only for a contact the user asked to put through.
 *   4. Everybody else is an unknown caller. The handset does not classify callers, so the only
 *      category it can honestly apply is `unknown`: blocked, then its posture, then the default.
 *
 * Postures become decisions like this. `reject` rejects. `pass_through` rings. `handle_with_agent`
 * also rings, because this path has no assistant to hand the call to, and hiding a call the user
 * did not ask to hide is worse than letting it ring. The user's hours change nothing here: they
 * decide when the assistant answers, and outside them a call rings (D-029) — as it does here anyway.
 */
object ScreeningRules {
  fun evaluate(
      snapshot: CallRulesSnapshot?,
      caller: ScreenedCaller,
      now: Instant,
      country: DialingCountry?,
  ): Screening {
    if (snapshot == null) {
      return Screening(ScreeningDecision.ALLOW, ScreeningReason.NO_RULES)
    }
    if (snapshot.isStaleAt(now)) {
      return Screening(ScreeningDecision.ALLOW, ScreeningReason.STALE_RULES)
    }

    val presented =
        when (caller) {
          ScreenedCaller.Withheld ->
              return decide(snapshot.anonymousPosture, ScreeningReason.WITHHELD_NUMBER)
          ScreenedCaller.NotDelivered ->
              return decide(HandlingPosture.PASS_THROUGH, ScreeningReason.NUMBER_NOT_DELIVERED)
          is ScreenedCaller.Presented -> caller.number
        }

    val contact = presented?.let { importantContact(snapshot, it, country) }
    if (contact != null) {
      return decide(contact.posture, ScreeningReason.IMPORTANT_CONTACT)
    }

    if (CallerCategory.UNKNOWN in snapshot.blockedCategories) {
      return Screening(ScreeningDecision.REJECT, ScreeningReason.BLOCKED_CATEGORY)
    }
    val categoryPosture = snapshot.postureByCategory[CallerCategory.UNKNOWN]
    return if (categoryPosture != null) {
      decide(categoryPosture, ScreeningReason.CATEGORY_POSTURE)
    } else {
      decide(snapshot.defaultPosture, ScreeningReason.DEFAULT_POSTURE)
    }
  }

  private fun importantContact(
      snapshot: CallRulesSnapshot,
      number: CallerNumber,
      country: DialingCountry?,
  ): ImportantContact? {
    val international = number.e164(country)
    return if (international != null) {
      snapshot.importantContacts.firstOrNull { it.phoneNumber == international }
    } else {
      snapshot.importantContacts.firstOrNull {
        it.posture == HandlingPosture.PASS_THROUGH && number.endsLike(it.phoneNumber)
      }
    }
  }

  private fun decide(posture: HandlingPosture, reason: ScreeningReason): Screening =
      if (posture == HandlingPosture.REJECT) {
        Screening(ScreeningDecision.REJECT, reason)
      } else {
        Screening(ScreeningDecision.ALLOW, reason)
      }
}
