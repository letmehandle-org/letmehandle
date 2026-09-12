package org.letmehandle.app.calls.rules

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class CallerNumberTest {
  private val stored = "+12025550143"

  @Test
  fun `an international number matches exactly`() {
    val number = CallerNumber.parse("+1 (202) 555-0143")!!
    assertEquals(stored, number.e164)
    assertTrue(number.matches(stored))
    assertFalse(number.matches("+12025550144"))
  }

  @Test
  fun `the international call prefix is read as a plus`() {
    assertEquals(stored, CallerNumber.parse("0012025550143")!!.e164)
  }

  @Test
  fun `a national number matches on its significant trailing digits`() {
    val number = CallerNumber.parse("202-555-0143")!!
    assertNull(number.e164)
    assertTrue(number.matches(stored))
    assertTrue(CallerNumber.parse("07700900123")!!.matches("+447700900123"))
  }

  @Test
  fun `too few digits match nobody`() {
    // A short code must not match somebody's mobile that happens to end the same way.
    assertFalse(CallerNumber.parse("50143")!!.matches(stored))
    // Seven digits, but a leading zero is a trunk prefix and not part of the subscriber's number.
    assertFalse(CallerNumber.parse("0550143")!!.matches(stored))
  }

  @Test
  fun `nothing usable is not a number`() {
    assertNull(CallerNumber.parse(null))
    assertNull(CallerNumber.parse(""))
    assertNull(CallerNumber.parse("*#06#"))
    assertNull(CallerNumber.parse("+0123"))
  }
}
