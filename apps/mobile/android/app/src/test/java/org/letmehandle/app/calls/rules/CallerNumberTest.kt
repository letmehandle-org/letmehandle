package org.letmehandle.app.calls.rules

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class CallerNumberTest {
  private val stored = "+12025550143"
  private val unitedStates = DialingCountry.of(networkIso = "us", simIso = null)
  private val unitedKingdom = DialingCountry.of(networkIso = "gb", simIso = null)

  @Test
  fun `an international number is its own E164 form, wherever the handset is`() {
    val number = CallerNumber.parse("+1 (202) 555-0143")!!
    assertEquals(stored, number.e164(country = null))
    assertEquals(stored, number.e164(unitedKingdom))
  }

  @Test
  fun `the international call prefix is read as a plus`() {
    assertEquals(stored, CallerNumber.parse("0012025550143")!!.e164(country = null))
  }

  @Test
  fun `a national number is international only once read in the handset's country`() {
    val number = CallerNumber.parse("202-555-0143")!!
    assertNull(number.e164(country = null))
    assertEquals(stored, number.e164(unitedStates))
    assertEquals("+447700900123", CallerNumber.parse("07700 900123")!!.e164(unitedKingdom))
  }

  @Test
  fun `a national number too short to be whole has no international form`() {
    assertNull(CallerNumber.parse("555-0143")!!.e164(unitedStates))
  }

  @Test
  fun `trailing digits agree only when enough of them do`() {
    assertTrue(CallerNumber.parse("202-555-0143")!!.endsLike(stored))
    // A short code must not match somebody's mobile that happens to end the same way.
    assertFalse(CallerNumber.parse("50143")!!.endsLike(stored))
    // Seven digits, but a leading zero is a trunk prefix and not part of the subscriber's number.
    assertFalse(CallerNumber.parse("0550143")!!.endsLike(stored))
  }

  @Test
  fun `a number is accepted as international exactly when the backend's E164 rule accepts it`() {
    val table =
        mapOf(
            "+12" to true, // the shortest: a country code and one digit
            "+" + "9".repeat(15) to true, // fifteen digits, the most there can be
            "+" + "9".repeat(16) to false,
            "+0" + "9".repeat(9) to false, // no country code starts with zero
            "+" to false,
            "+1" to false,
            "+1202555014a" to false,
            "+١٢" to false, // digits, but not ones anybody dials
        )
    table.forEach { (candidate, accepted) ->
      assertEquals(candidate, accepted, CallerNumber.E164.matches(candidate))
      assertEquals(candidate, if (accepted) candidate else null, CallerNumber.parse(candidate)?.e164(country = null))
    }
  }

  @Test
  fun `nothing usable is not a number`() {
    assertNull(CallerNumber.parse(null))
    assertNull(CallerNumber.parse(""))
    assertNull(CallerNumber.parse("*#06#"))
    assertNull(CallerNumber.parse("+0123"))
  }
}
