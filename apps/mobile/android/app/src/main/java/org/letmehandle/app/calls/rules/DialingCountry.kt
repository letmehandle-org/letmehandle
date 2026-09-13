package org.letmehandle.app.calls.rules

/** The country whose national numbers the network delivers, for the numbering plans read unambiguously. */
class DialingCountry private constructor(private val plan: NumberingPlan) {

  /** The national [digits] in E.164, or null when they are not a whole national number. */
  fun international(digits: String): String? = plan.international(digits)

  companion object {
    /** The network's country, or the SIM's when the network gives none; null for a country with no plan here. */
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

/** The North American plan: ten digits not starting with 0 or 1, optionally after a 1. */
private data object NorthAmerican : NumberingPlan {
  private val national = Regex("^1?([2-9]\\d{9})$")

  override fun international(digits: String): String? = national.matchEntire(digits)?.let { "+1${it.groupValues[1]}" }
}

/** A plan whose whole national numbers start with a trunk prefix. */
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
