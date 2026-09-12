package org.letmehandle.app.calls.rules

/**
 * A caller's number as the network delivered it, which is not always international.
 *
 * The backend stores important contacts in E.164 only, so a contact is recognised by comparing
 * international forms, never raw ones. A network often delivers a domestic caller in national form,
 * without the country code, and that form is only international once it is read in the country the
 * network is in ([DialingCountry]). When it cannot be — no country known, or a number too short to
 * be a whole national one — all that is left is whether its trailing digits agree with a stored
 * number, which is a guess: a caller abroad, or in another area code, can share them.
 */
class CallerNumber private constructor(private val digits: String, private val isInternational: Boolean) {

  /**
   * The number in E.164: as delivered when it came international, or its national form read in
   * [country]. Null when it cannot be placed, and always a value the backend accepts when not.
   */
  fun e164(country: DialingCountry?): String? =
      if (isInternational) "+$digits" else country?.international(digits)?.takeIf(E164::matches)

  /**
   * Whether this number's significant trailing digits are the end of [storedE164], with a floor on
   * how many must agree so that a short code cannot match somebody's mobile. Evidence, not proof.
   */
  fun endsLike(storedE164: String): Boolean {
    val significant = digits.trimStart('0')
    return significant.length >= MIN_NATIONAL_DIGITS && storedE164.removePrefix("+").endsWith(significant)
  }

  companion object {
    /** Fewer agreeing digits than this is a coincidence rather than a match. */
    const val MIN_NATIONAL_DIGITS = 7

    /**
     * E.164 exactly as the backend's `PhoneNumber` states it: a plus, a first digit that is not 0,
     * and at most fifteen digits in all. ASCII digits only, because only those are dialled.
     */
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
