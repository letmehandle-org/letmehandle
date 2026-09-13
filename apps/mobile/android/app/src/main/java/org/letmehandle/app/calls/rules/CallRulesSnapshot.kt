package org.letmehandle.app.calls.rules

import java.time.Duration
import java.time.Instant
import org.letmehandle.app.calls.WireValue

/** The user's deterministic call rules as stored on the handset; mirrors the backend's `CallRules` (D-028). */
data class CallRulesSnapshot(
    val syncedAt: Instant,
    val defaultPosture: HandlingPosture,
    val anonymousPosture: HandlingPosture,
    val postureByCategory: Map<CallerCategory, HandlingPosture>,
    val blockedCategories: Set<CallerCategory>,
    val importantContacts: List<ImportantContact>,
) {
  init {
    require((blockedCategories intersect postureByCategory.keys).isEmpty()) {
      "a category is either blocked or given a posture, never both"
    }
  }

  /** Whether these rules are too old, or dated too far ahead, to refuse a caller on. */
  fun isStaleAt(now: Instant): Boolean =
      syncedAt > now + CLOCK_SKEW_ALLOWANCE || Duration.between(syncedAt, now) > MAX_AGE

  companion object {
    /** The age beyond which a snapshot refuses nobody. */
    val MAX_AGE: Duration = Duration.ofDays(7)

    /** How far ahead of the handset's clock a snapshot may be dated before it counts as stale. */
    val CLOCK_SKEW_ALLOWANCE: Duration = Duration.ofMinutes(5)
  }
}

/** What should happen to a call before anybody has spoken to it. Mirrors `HandlingPosture`. */
enum class HandlingPosture(override val wire: String) : WireValue {
  PASS_THROUGH("pass_through"),
  HANDLE_WITH_AGENT("handle_with_agent"),
  REJECT("reject"),
}

/** What kind of caller this is. Mirrors `CallerCategory`. */
enum class CallerCategory(override val wire: String) : WireValue {
  KNOWN_CONTACT("known_contact"),
  DELIVERY("delivery"),
  HEALTHCARE("healthcare"),
  EDUCATION("education"),
  FINANCIAL("financial"),
  SERVICE_PROVIDER("service_provider"),
  SALES("sales"),
  SPAM("spam"),
  UNKNOWN("unknown"),
}

/** Somebody the user has told the assistant about, by number. */
data class ImportantContact(val phoneNumber: String, val posture: HandlingPosture)
