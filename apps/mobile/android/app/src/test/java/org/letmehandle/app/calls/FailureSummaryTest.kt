package org.letmehandle.app.calls

import org.json.JSONException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class FailureSummaryTest {
  @Test
  fun `a failure is named by its kinds, never by messages that can quote stored numbers`() {
    // What org.json says about stored text it cannot read: it quotes the text.
    val unreadable = JSONException("Expected a ',' at 31 [character 32 line 1] {\"caller_number\":\"+12025550145\"")
    val failure = IllegalStateException("call screening state could not be written", unreadable)

    val summary = FailureSummary.of(failure)

    assertEquals("java.lang.IllegalStateException <- org.json.JSONException", summary)
    assertFalse(summary.contains("2025550145"))
  }
}
