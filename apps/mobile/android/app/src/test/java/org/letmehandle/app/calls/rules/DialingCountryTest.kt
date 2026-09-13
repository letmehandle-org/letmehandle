package org.letmehandle.app.calls.rules

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class DialingCountryTest {
  private fun read(iso: String, national: String): String? = DialingCountry.of(networkIso = iso, simIso = null)!!.international(national)

  @Test
  fun `whole national numbers are read into E164, and anything that might be local is not`() {
    val table =
        listOf(
            // North America: ten digits, or eleven after the trunk prefix 1.
            Triple("us", "2025550143", "+12025550143"),
            Triple("us", "12025550143", "+12025550143"),
            Triple("ca", "3035550143", "+13035550143"),
            Triple("us", "5550143", null), // seven digits: a local call without its area code
            Triple("us", "0205550143", null), // an area code never starts with 0
            Triple("us", "22025550143", null),
            // A trunk-prefix plan: the prefix is what says the number is whole.
            Triple("gb", "07700900123", "+447700900123"),
            Triple("gb", "02079460123", "+442079460123"),
            Triple("gb", "7700900123", null),
            Triple("gb", "0", null),
            // A country's calling code is its own, not the handset owner's.
            Triple("GB", "07700900123", "+447700900123"),
        )
    table.forEach { (iso, national, expected) -> assertEquals("$iso $national", expected, read(iso, national)) }
  }

  @Test
  fun `the network's country is used, and the SIM's only when the network has not said`() {
    val roaming = DialingCountry.of(networkIso = "gb", simIso = "us")!!
    assertEquals("+447700900123", roaming.international("07700900123"))
    assertNull(roaming.international("2025550143"))

    val unregistered = DialingCountry.of(networkIso = "", simIso = "us")!!
    assertEquals("+12025550143", unregistered.international("2025550143"))
  }

  @Test
  fun `a country the handset cannot read numbers for is no country at all`() {
    assertNull(DialingCountry.of(networkIso = null, simIso = null))
    assertNull(DialingCountry.of(networkIso = "", simIso = " "))
    // Not the SIM's in its place: that would read this network's numbers by another plan.
    assertNull(DialingCountry.of(networkIso = "zz", simIso = "us"))
  }
}
