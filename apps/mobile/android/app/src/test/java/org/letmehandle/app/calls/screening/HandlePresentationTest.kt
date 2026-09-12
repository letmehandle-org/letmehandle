package org.letmehandle.app.calls.screening

import android.telecom.TelecomManager
import java.time.Instant
import org.junit.Assert.assertEquals
import org.junit.Test
import org.letmehandle.app.calls.rules.CallRulesSnapshot
import org.letmehandle.app.calls.rules.HandlingPosture
import org.letmehandle.app.calls.rules.ScreeningDecision
import org.letmehandle.app.calls.rules.ScreeningRules

class HandlePresentationTest {
  private val now = Instant.parse("2026-09-13T11:00:00Z")

  // Everybody anonymous is refused; everybody else rings.
  private val refuseAnonymous =
      CallRulesSnapshot(
          syncedAt = now,
          defaultPosture = HandlingPosture.PASS_THROUGH,
          anonymousPosture = HandlingPosture.REJECT,
          postureByCategory = emptyMap(),
          blockedCategories = emptySet(),
          quietHours = null,
          importantContacts = emptyList(),
      )

  private fun decisionFor(presentation: Int, handleNumber: String?): ScreeningDecision =
      ScreeningRules.evaluate(refuseAnonymous, HandlePresentation.callerOf(presentation, handleNumber), now).decision

  @Test
  fun `a caller who restricted their number is anonymous`() {
    assertEquals(ScreeningDecision.REJECT, decisionFor(TelecomManager.PRESENTATION_RESTRICTED, null))
  }

  @Test
  fun `a number the network did not deliver is not a caller hiding one, and rings`() {
    assertEquals(ScreeningDecision.ALLOW, decisionFor(TelecomManager.PRESENTATION_UNKNOWN, null))
    assertEquals(ScreeningDecision.ALLOW, decisionFor(TelecomManager.PRESENTATION_UNAVAILABLE, null))
  }

  @Test
  fun `a payphone rings`() {
    assertEquals(ScreeningDecision.ALLOW, decisionFor(TelecomManager.PRESENTATION_PAYPHONE, null))
  }

  @Test
  fun `a presented number is screened on its number`() {
    assertEquals(ScreeningDecision.ALLOW, decisionFor(TelecomManager.PRESENTATION_ALLOWED, "+12025550145"))
  }
}
