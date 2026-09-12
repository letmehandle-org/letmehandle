package org.letmehandle.app.calls.rules

/**
 * A caller's number as the network delivered it, which is not always international.
 *
 * The backend stores important contacts in E.164 only. A network often delivers a domestic
 * caller in national form, without the country code, so an exact comparison would miss the
 * user's own contacts on exactly the calls that matter. Matching is therefore on the trailing
 * digits when the delivered number has no country code, with a floor on how many digits must
 * agree so that a short code cannot match somebody's mobile.
 */
class CallerNumber private constructor(private val digits: String, val isInternational: Boolean) {

  /** The number in E.164, or null when it arrived without a country code. */
  val e164: String?
    get() = if (isInternational) "+$digits" else null

  fun matches(storedE164: String): Boolean {
    val stored = storedE164.removePrefix("+")
    return if (isInternational) {
      stored == digits
    } else {
      val significant = digits.trimStart('0')
      significant.length >= MIN_NATIONAL_DIGITS && stored.endsWith(significant)
    }
  }

  companion object {
    /** Fewer agreeing digits than this is a coincidence rather than a match. */
    const val MIN_NATIONAL_DIGITS = 7

    private val decoration = Regex("[\\s\\-().]")
    private val international = Regex("^\\+[1-9]\\d{1,14}$")
    private val national = Regex("^\\d{3,15}$")

    /** Reads what a `tel:` handle carries. Null when there is nothing usable in it. */
    fun parse(raw: String?): CallerNumber? {
      val candidate = decoration.replace(raw?.trim().orEmpty(), "")
      val normalised = if (candidate.startsWith("00")) "+" + candidate.drop(2) else candidate
      return when {
        international.matches(normalised) -> CallerNumber(normalised.drop(1), isInternational = true)
        national.matches(normalised) -> CallerNumber(normalised, isInternational = false)
        else -> null
      }
    }
  }
}
