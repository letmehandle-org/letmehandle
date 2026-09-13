package org.letmehandle.app.calls.rules

/** A caller's number as delivered, international or national. */
class CallerNumber private constructor(private val digits: String, private val isInternational: Boolean) {

  /** The number in E.164, reading a national form in [country]; null when it cannot be placed. */
  fun e164(country: DialingCountry?): String? =
      if (isInternational) "+$digits" else country?.international(digits)?.takeIf(E164::matches)

  /** Whether at least [MIN_NATIONAL_DIGITS] significant trailing digits match [storedE164]. */
  fun endsLike(storedE164: String): Boolean {
    val significant = digits.trimStart('0')
    return significant.length >= MIN_NATIONAL_DIGITS && storedE164.removePrefix("+").endsWith(significant)
  }

  companion object {
    /** Fewer agreeing digits than this is a coincidence rather than a match. */
    const val MIN_NATIONAL_DIGITS = 7

    /** E.164 as the backend's `PhoneNumber` accepts it, in ASCII digits. */
    val E164 = Regex("^\\+[1-9][0-9]{1,14}$")

    private val decoration = Regex("[\\s\\-().]")
    private val national = Regex("^[0-9]{3,15}$")

    /** Reads what a `tel:` handle carries. Null when there is nothing usable in it. */
    fun parse(raw: String?): CallerNumber? {
      val candidate = decoration.replace(raw?.trim().orEmpty(), "")
      val normalised = if (candidate.startsWith("00")) "+" + candidate.drop(2) else candidate
      return when {
        E164.matches(normalised) -> CallerNumber(normalised.drop(1), isInternational = true)
        national.matches(normalised) -> CallerNumber(normalised, isInternational = false)
        else -> null
      }
    }
  }
}
