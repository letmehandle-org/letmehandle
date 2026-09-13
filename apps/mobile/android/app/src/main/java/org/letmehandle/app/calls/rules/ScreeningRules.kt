package org.letmehandle.app.calls.rules

import java.time.Instant
import org.letmehandle.app.calls.WireValue

/** What was done with a call before it rang. Mirrors `ScreeningDecision`. */
enum class ScreeningDecision(override val wire: String) : WireValue {
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

/** Who is calling, as far as a screening service is told. */
sealed interface ScreenedCaller {
  /** A presented number; [number] is null when its form cannot be read. */
  data class Presented(val number: CallerNumber?) : ScreenedCaller

  /** The caller chose not to present their number. */
  data object Withheld : ScreenedCaller

  /** No number arrived, without the caller choosing it. */
  data object NotDelivered : ScreenedCaller
}

/** Applies the rules to one call: no or stale rules ring, then withheld, important contact, then the unknown category (D-028). */
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
