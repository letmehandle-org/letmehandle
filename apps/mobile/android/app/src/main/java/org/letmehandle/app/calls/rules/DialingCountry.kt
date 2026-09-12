package org.letmehandle.app.calls.rules

/**
 * The country whose national numbers the network is delivering, and how to read one of them
 * internationally.
 *
 * A network delivers a domestic caller without the country code, in the form people in that
 * country dial. Written in E.164 that number is the country's calling code followed by the
 * national number without its trunk prefix — but only when the delivered form is the whole
 * national number. A number dialled locally, without its area code, has no single international
 * form, and guessing one is how a stranger comes to be taken for a contact. So each plan here
 * reads only the forms that cannot mean anything else, and gives up on the rest.
 *
 * Deliberately a short list. A country missing from it is read as unknown, which costs its users
 * nothing but a contact matched less often: an unplaced number can still let a call ring (see
 * [ScreeningRules]), and can never refuse one. A country listed wrongly would be worse, so a plan
 * is only here when its national form is unambiguous.
 */
class DialingCountry private constructor(private val plan: NumberingPlan) {

  /** The national [digits] in E.164, or null when they are not a whole national number. */
  fun international(digits: String): String? = plan.international(digits)

  companion object {
    /**
     * The country the handset is dialling in: the network's, which is where a delivered national
     * number is national to, and the SIM's only when the network has not said.
     *
     * A network country this does not know is not replaced by the SIM's. Roaming is exactly when
     * the two differ, and reading the network's numbers by the SIM's plan is reading them wrongly.
     */
    fun of(networkIso: String?, simIso: String?): DialingCountry? {
      val iso = networkIso?.takeIf { it.isNotBlank() } ?: simIso?.takeIf { it.isNotBlank() } ?: return null
      return PLANS[iso.lowercase()]?.let(::DialingCountry)
    }

    private val PLANS: Map<String, NumberingPlan> =
        buildMap {
          listOf(
                  "us", "ca", "pr", "vi", "gu", "as", "mp", "ag", "ai", "bb", "bm", "bs", "dm", "do",
                  "gd", "jm", "kn", "ky", "lc", "ms", "sx", "tc", "tt", "vc", "vg",
              )
              .forEach { put(it, NorthAmerican) }
          mapOf(
                  "gb" to "44", "ie" to "353", "fr" to "33", "de" to "49", "at" to "43", "ch" to "41",
                  "nl" to "31", "be" to "32", "se" to "46", "fi" to "358", "in" to "91", "au" to "61",
                  "nz" to "64", "za" to "27", "jp" to "81", "kr" to "82", "cn" to "86", "tw" to "886",
                  "tr" to "90", "il" to "972", "eg" to "20", "ng" to "234", "ke" to "254", "pk" to "92",
                  "bd" to "880", "id" to "62", "my" to "60", "th" to "66", "ph" to "63", "vn" to "84",
                  "ae" to "971", "sa" to "966", "ua" to "380", "ro" to "40", "bg" to "359", "hr" to "385",
                  "rs" to "381", "si" to "386", "sk" to "421",
              )
              .forEach { (iso, code) -> put(iso, TrunkPrefixed(code, trunkPrefix = "0")) }
          mapOf("ru" to "7", "kz" to "7").forEach { (iso, code) -> put(iso, TrunkPrefixed(code, trunkPrefix = "8")) }
          put("hu", TrunkPrefixed("36", trunkPrefix = "06"))
          mapOf(
                  "es" to "34", "it" to "39", "pt" to "351", "gr" to "30", "dk" to "45", "no" to "47",
                  "pl" to "48", "cz" to "420", "lu" to "352", "sg" to "65", "hk" to "852", "qa" to "974",
              )
              .forEach { (iso, code) -> put(iso, Closed(code)) }
        }
  }
}

private sealed interface NumberingPlan {
  fun international(digits: String): String?
}

/**
 * The North American plan: ten digits, the first of them never 0 or 1, optionally after the
 * trunk prefix 1. Seven digits is a local call without its area code, and is not read.
 */
private data object NorthAmerican : NumberingPlan {
  private val national = Regex("^1?([2-9]\\d{9})$")

  override fun international(digits: String): String? = national.matchEntire(digits)?.let { "+1${it.groupValues[1]}" }
}

/**
 * A plan whose whole national numbers are dialled after a trunk prefix. A number without the
 * prefix is a local one, missing an area code nobody can supply.
 */
private data class TrunkPrefixed(val callingCode: String, val trunkPrefix: String) : NumberingPlan {
  override fun international(digits: String): String? =
      if (digits.startsWith(trunkPrefix) && digits.length > trunkPrefix.length) {
        "+$callingCode${digits.removePrefix(trunkPrefix)}"
      } else {
        null
      }
}

/** A closed plan: every number is dialled whole and without a prefix, so a national number is complete. */
private data class Closed(val callingCode: String) : NumberingPlan {
  override fun international(digits: String): String = "+$callingCode$digits"
}
