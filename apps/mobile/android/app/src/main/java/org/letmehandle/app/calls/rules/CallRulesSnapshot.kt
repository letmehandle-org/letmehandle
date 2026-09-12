package org.letmehandle.app.calls.rules

import java.time.Duration
import java.time.Instant
import java.time.LocalTime
import java.time.ZoneId

/**
 * The user's deterministic call rules, as the handset holds them.
 *
 * Written by the app after it reads the preferences API, and read by the screening service when
 * a call arrives. A copy rather than a request: the platform gives the service five seconds, and
 * a network round trip is not something to spend them on.
 *
 * Mirrors the backend's `CallRules` and `ImportantContact` in `domain/models/preferences.py`,
 * limited to what a decision before ringing reads. Labels are left behind on purpose: the
 * handset needs a number and what to do with it, not what the user calls the person.
 */
data class CallRulesSnapshot(
    val syncedAt: Instant,
    val defaultPosture: HandlingPosture,
    val anonymousPosture: HandlingPosture,
    val postureByCategory: Map<CallerCategory, HandlingPosture>,
    val blockedCategories: Set<CallerCategory>,
    val quietHours: QuietHours?,
    val importantContacts: List<ImportantContact>,
) {
  init {
    require((blockedCategories intersect postureByCategory.keys).isEmpty()) {
      "a category is either blocked or given a posture, never both"
    }
  }

  /** Whether these rules are too old to refuse a caller on. */
  fun isStaleAt(now: Instant): Boolean = Duration.between(syncedAt, now) > MAX_AGE

  companion object {
    /**
     * How long a snapshot may go unrefreshed before it stops being trusted to refuse anybody.
     *
     * The app refreshes it whenever it opens. A handset whose app has not opened for a week may
     * be carrying rules the user has since changed, and ringing is the recoverable mistake.
     */
    val MAX_AGE: Duration = Duration.ofDays(7)
  }
}

/** What should happen to a call before anybody has spoken to it. Mirrors `HandlingPosture`. */
enum class HandlingPosture(val wire: String) {
  PASS_THROUGH("pass_through"),
  HANDLE_WITH_AGENT("handle_with_agent"),
  REJECT("reject");

  companion object {
    fun fromWire(value: String): HandlingPosture =
        entries.firstOrNull { it.wire == value }
            ?: throw IllegalArgumentException("unknown posture")
  }
}

/** What kind of caller this is. Mirrors `CallerCategory`. */
enum class CallerCategory(val wire: String) {
  KNOWN_CONTACT("known_contact"),
  DELIVERY("delivery"),
  HEALTHCARE("healthcare"),
  EDUCATION("education"),
  FINANCIAL("financial"),
  SERVICE_PROVIDER("service_provider"),
  SALES("sales"),
  SPAM("spam"),
  UNKNOWN("unknown");

  companion object {
    fun fromWire(value: String): CallerCategory =
        entries.firstOrNull { it.wire == value }
            ?: throw IllegalArgumentException("unknown caller category")
  }
}

/** Somebody the user has told the assistant about, by number. */
data class ImportantContact(val phoneNumber: String, val posture: HandlingPosture)

/**
 * A daily window in the user's own timezone, to the minute. Mirrors `TimeWindow`.
 *
 * It may wrap past midnight, which is the ordinary case for quiet hours.
 */
data class QuietHours(val start: LocalTime, val end: LocalTime, val zone: ZoneId) {
  init {
    require(start != end) { "a window that starts and ends at the same moment covers nothing" }
  }

  fun contains(instant: Instant): Boolean {
    val local = instant.atZone(zone).toLocalTime()
    return if (end < start) local >= start || local < end else local >= start && local < end
  }
}
