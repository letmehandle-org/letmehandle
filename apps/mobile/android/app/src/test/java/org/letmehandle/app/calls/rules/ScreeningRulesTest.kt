package org.letmehandle.app.calls.rules

import java.time.Duration
import java.time.Instant
import org.junit.Assert.assertEquals
import org.junit.Test

class ScreeningRulesTest {
  // Noon in London.
  private val noon = Instant.parse("2026-09-13T11:00:00Z")
  // Half past eleven at night in London.
  private val lateEvening = Instant.parse("2026-09-13T22:30:00Z")

  private val contact = "+12025550143"
  private val stranger = ScreenedCaller.Presented(CallerNumber.parse("+12025550199"))
  private val withheld = ScreenedCaller.Withheld

  private fun rules(
      defaultPosture: HandlingPosture = HandlingPosture.HANDLE_WITH_AGENT,
      anonymousPosture: HandlingPosture = HandlingPosture.HANDLE_WITH_AGENT,
      postureByCategory: Map<CallerCategory, HandlingPosture> = emptyMap(),
      blocked: Set<CallerCategory> = emptySet(),
      contacts: List<ImportantContact> = emptyList(),
      syncedAt: Instant = noon.minusSeconds(3_600),
  ) =
      CallRulesSnapshot(
          syncedAt = syncedAt,
          defaultPosture = defaultPosture,
          anonymousPosture = anonymousPosture,
          postureByCategory = postureByCategory,
          blockedCategories = blocked,
          importantContacts = contacts,
      )

  private val unitedStates = DialingCountry.of(networkIso = "us", simIso = null)

  private fun decide(
      snapshot: CallRulesSnapshot?,
      caller: ScreenedCaller = stranger,
      at: Instant = noon,
      country: DialingCountry? = unitedStates,
  ) = ScreeningRules.evaluate(snapshot, caller, at, country)

  @Test
  fun `with no rules the call rings`() {
    assertEquals(Screening(ScreeningDecision.ALLOW, ScreeningReason.NO_RULES), decide(null))
  }

  @Test
  fun `rules too old to vouch for never refuse anybody`() {
    val stale = rules(defaultPosture = HandlingPosture.REJECT, syncedAt = noon.minus(CallRulesSnapshot.MAX_AGE).minusSeconds(1))
    assertEquals(Screening(ScreeningDecision.ALLOW, ScreeningReason.STALE_RULES), decide(stale))
  }

  @Test
  fun `rules exactly at their age limit are still applied`() {
    val oldest = rules(defaultPosture = HandlingPosture.REJECT, syncedAt = noon.minus(CallRulesSnapshot.MAX_AGE))
    assertEquals(ScreeningDecision.REJECT, decide(oldest).decision)
  }

  @Test
  fun `rules synced in the future, by a clock since corrected, never refuse anybody`() {
    val ahead = rules(defaultPosture = HandlingPosture.REJECT, syncedAt = noon.plus(Duration.ofDays(30)))
    assertEquals(Screening(ScreeningDecision.ALLOW, ScreeningReason.STALE_RULES), decide(ahead))
  }

  @Test
  fun `rules synced just past the skew allowance are stale, and just within it are applied`() {
    val beyond = noon.plus(CallRulesSnapshot.CLOCK_SKEW_ALLOWANCE).plusSeconds(1)
    assertEquals(ScreeningReason.STALE_RULES, decide(rules(defaultPosture = HandlingPosture.REJECT, syncedAt = beyond)).reason)
    val within = noon.plus(CallRulesSnapshot.CLOCK_SKEW_ALLOWANCE)
    assertEquals(ScreeningDecision.REJECT, decide(rules(defaultPosture = HandlingPosture.REJECT, syncedAt = within)).decision)
  }

  @Test
  fun `a withheld number gets the anonymous posture`() {
    val snapshot = rules(defaultPosture = HandlingPosture.PASS_THROUGH, anonymousPosture = HandlingPosture.REJECT)
    assertEquals(Screening(ScreeningDecision.REJECT, ScreeningReason.WITHHELD_NUMBER), decide(snapshot, withheld))
    assertEquals(ScreeningDecision.ALLOW, decide(snapshot, stranger).decision)
  }

  @Test
  fun `a number the network did not deliver is never rejected`() {
    val snapshot =
        rules(
            defaultPosture = HandlingPosture.REJECT,
            anonymousPosture = HandlingPosture.REJECT,
            blocked = setOf(CallerCategory.UNKNOWN),
        )
    assertEquals(
        Screening(ScreeningDecision.ALLOW, ScreeningReason.NUMBER_NOT_DELIVERED),
        decide(snapshot, ScreenedCaller.NotDelivered),
    )
  }

  @Test
  fun `an unreadable number is not an anonymous one`() {
    val unreadable = ScreenedCaller.Presented(number = null)
    val snapshot = rules(defaultPosture = HandlingPosture.PASS_THROUGH, anonymousPosture = HandlingPosture.REJECT)
    assertEquals(Screening(ScreeningDecision.ALLOW, ScreeningReason.DEFAULT_POSTURE), decide(snapshot, unreadable))
  }

  @Test
  fun `an important contact gets their own posture ahead of the default`() {
    val snapshot =
        rules(
            defaultPosture = HandlingPosture.REJECT,
            contacts = listOf(ImportantContact(contact, HandlingPosture.PASS_THROUGH)),
        )
    val caller = ScreenedCaller.Presented(CallerNumber.parse(contact))
    assertEquals(Screening(ScreeningDecision.ALLOW, ScreeningReason.IMPORTANT_CONTACT), decide(snapshot, caller))
  }

  @Test
  fun `a contact to put through rings at night too`() {
    val snapshot = rules(contacts = listOf(ImportantContact(contact, HandlingPosture.PASS_THROUGH)))
    val caller = ScreenedCaller.Presented(CallerNumber.parse(contact))
    assertEquals(ScreeningDecision.ALLOW, decide(snapshot, caller, lateEvening).decision)
  }

  @Test
  fun `a contact the user rejects is rejected`() {
    val snapshot = rules(contacts = listOf(ImportantContact(contact, HandlingPosture.REJECT)))
    val caller = ScreenedCaller.Presented(CallerNumber.parse("202-555-0143"))
    assertEquals(Screening(ScreeningDecision.REJECT, ScreeningReason.IMPORTANT_CONTACT), decide(snapshot, caller))
  }

  private data class MatchCase(
      val name: String,
      val delivered: String,
      val country: DialingCountry?,
      val contact: ImportantContact,
      val at: Instant,
      val expected: Screening,
  )

  @Test
  fun `a contact is recognised in international form, and a guess from trailing digits only ever rings`() {
    // Unknown callers ring, so any refusal below comes from matching a contact.
    val rejectedAbroad = ImportantContact("+447700900143", HandlingPosture.REJECT)
    val rejectedInDenver = ImportantContact("+13035550143", HandlingPosture.REJECT)
    val putThroughInDenver = ImportantContact("+13035550143", HandlingPosture.PASS_THROUGH)
    val handledInDenver = ImportantContact("+13035550143", HandlingPosture.HANDLE_WITH_AGENT)
    val ringsAsUnknown = Screening(ScreeningDecision.ALLOW, ScreeningReason.DEFAULT_POSTURE)
    val table =
        listOf(
            // A national caller in the US shares ten trailing digits with a UK number: read as
            // +1…, they are somebody else.
            MatchCase("national caller, contact abroad", "770-090-0143", unitedStates, rejectedAbroad, noon, ringsAsUnknown),
            // And with no country to read it by, a guess cannot reject.
            MatchCase("national caller, no country", "770-090-0143", null, rejectedAbroad, noon, ringsAsUnknown),
            // Seven digits is not a whole number anywhere in North America.
            MatchCase("seven digits, rejected contact", "555-0143", unitedStates, rejectedInDenver, noon, ringsAsUnknown),
            MatchCase(
                "seven digits, contact put through",
                "555-0143",
                unitedStates,
                putThroughInDenver,
                lateEvening,
                Screening(ScreeningDecision.ALLOW, ScreeningReason.IMPORTANT_CONTACT),
            ),
            // A contact whose posture could hide the call is not guessed at: the caller is unknown.
            MatchCase("seven digits, contact handled", "555-0143", unitedStates, handledInDenver, noon, ringsAsUnknown),
            // Placed exactly, a contact's own posture applies in full.
            MatchCase(
                "national caller read in its country",
                "303-555-0143",
                unitedStates,
                rejectedInDenver,
                noon,
                Screening(ScreeningDecision.REJECT, ScreeningReason.IMPORTANT_CONTACT),
            ),
            MatchCase(
                "international caller",
                "+1 303 555 0143",
                null,
                rejectedInDenver,
                noon,
                Screening(ScreeningDecision.REJECT, ScreeningReason.IMPORTANT_CONTACT),
            ),
            MatchCase("international caller, another country", "+1 770 090 0143", null, rejectedAbroad, noon, ringsAsUnknown),
        )
    table.forEach { case ->
      val snapshot =
          rules(defaultPosture = HandlingPosture.PASS_THROUGH, contacts = listOf(case.contact))
      val caller = ScreenedCaller.Presented(CallerNumber.parse(case.delivered))
      assertEquals(case.name, case.expected, decide(snapshot, caller, case.at, case.country))
    }
  }

  @Test
  fun `a contact handled by the assistant rings, because this path has no assistant`() {
    val snapshot = rules(contacts = listOf(ImportantContact(contact, HandlingPosture.HANDLE_WITH_AGENT)))
    val caller = ScreenedCaller.Presented(CallerNumber.parse(contact))
    assertEquals(Screening(ScreeningDecision.ALLOW, ScreeningReason.IMPORTANT_CONTACT), decide(snapshot, caller, lateEvening))
  }

  @Test
  fun `blocking unknown callers rejects everybody the handset cannot place`() {
    val snapshot = rules(defaultPosture = HandlingPosture.PASS_THROUGH, blocked = setOf(CallerCategory.UNKNOWN))
    assertEquals(Screening(ScreeningDecision.REJECT, ScreeningReason.BLOCKED_CATEGORY), decide(snapshot))
  }

  @Test
  fun `a blocked category the handset cannot recognise does not reject anybody`() {
    // The handset does not classify callers. Spam blocked on the backend is a rule for the
    // assistant; applying it here would mean guessing who is spam.
    val snapshot = rules(defaultPosture = HandlingPosture.PASS_THROUGH, blocked = setOf(CallerCategory.SPAM))
    assertEquals(Screening(ScreeningDecision.ALLOW, ScreeningReason.DEFAULT_POSTURE), decide(snapshot))
  }

  @Test
  fun `the unknown category's posture comes before the default`() {
    val snapshot =
        rules(
            defaultPosture = HandlingPosture.PASS_THROUGH,
            postureByCategory = mapOf(CallerCategory.UNKNOWN to HandlingPosture.REJECT),
        )
    assertEquals(Screening(ScreeningDecision.REJECT, ScreeningReason.CATEGORY_POSTURE), decide(snapshot))
  }

  @Test
  fun `a call for the assistant rings, because this path has no assistant`() {
    val snapshot = rules(defaultPosture = HandlingPosture.HANDLE_WITH_AGENT)
    assertEquals(Screening(ScreeningDecision.ALLOW, ScreeningReason.DEFAULT_POSTURE), decide(snapshot))
  }

  @Test
  fun `nothing the rules decide silences a call`() {
    // Silencing was how quiet hours were kept; the user's hours now decide only when the assistant
    // answers (D-029), and this path answers nothing, so every posture, at every hour, rings or
    // is rejected.
    HandlingPosture.entries.forEach { posture ->
      listOf(noon, lateEvening).forEach { at ->
        val decision = decide(rules(defaultPosture = posture), at = at).decision
        assertEquals(if (posture == HandlingPosture.REJECT) ScreeningDecision.REJECT else ScreeningDecision.ALLOW, decision)
      }
    }
  }

  @Test(expected = IllegalArgumentException::class)
  fun `a category both blocked and given a posture is refused`() {
    rules(blocked = setOf(CallerCategory.UNKNOWN), postureByCategory = mapOf(CallerCategory.UNKNOWN to HandlingPosture.REJECT))
  }
}
