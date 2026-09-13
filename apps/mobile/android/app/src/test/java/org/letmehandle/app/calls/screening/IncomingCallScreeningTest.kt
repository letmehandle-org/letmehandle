package org.letmehandle.app.calls.screening

import java.time.Instant
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Test
import org.letmehandle.app.calls.rules.CallRulesSnapshot
import org.letmehandle.app.calls.rules.CallerNumber
import org.letmehandle.app.calls.rules.DialingCountry
import org.letmehandle.app.calls.rules.HandlingPosture
import org.letmehandle.app.calls.rules.ScreenedCaller
import org.letmehandle.app.calls.rules.ScreeningDecision
import org.letmehandle.app.calls.rules.ScreeningReason

class IncomingCallScreeningTest {
  private data class Recorded(val callerNumber: String?, val decision: ScreeningDecision, val occurredAt: Instant)

  private val worker = Executors.newSingleThreadExecutor()
  private val timer = Executors.newSingleThreadScheduledExecutor()
  private val arrival = Instant.parse("2026-09-13T11:00:00Z")
  private val ticks = AtomicLong()
  private val recorded = LinkedBlockingQueue<Recorded>()
  private val responses = LinkedBlockingQueue<ScreeningReason>()

  private val rejectEverybody =
      CallRulesSnapshot(
          syncedAt = arrival,
          defaultPosture = HandlingPosture.REJECT,
          anonymousPosture = HandlingPosture.REJECT,
          postureByCategory = emptyMap(),
          blockedCategories = emptySet(),
          importantContacts = emptyList(),
      )

  @After
  fun stop() {
    worker.shutdownNow()
    timer.shutdownNow()
  }

  /** A screening whose clock moves a minute on every reading. */
  private fun screening(budgetMillis: Long, readSnapshot: () -> CallRulesSnapshot?) =
      IncomingCallScreening(
          screener = DeadlineScreener(worker, timer, budgetMillis),
          clock = { arrival.plusSeconds(60 * ticks.getAndIncrement()) },
          readSnapshot = readSnapshot,
          record = { number, decision, at -> recorded.add(Recorded(number, decision, at)) },
          onFailure = { throw AssertionError("screening failed", it) },
      )

  @Test
  fun `a decision is recorded as of the call's arrival, not of when it was written`() {
    screening(budgetMillis = 1_000) { rejectEverybody }
        .screen(ScreenedCaller.Presented(CallerNumber.parse("+12025550145")), { null }) { responses.add(it.reason) }

    assertEquals(Recorded("+12025550145", ScreeningDecision.REJECT, arrival), recorded.poll(1, TimeUnit.SECONDS))
  }

  @Test
  fun `a timed-out call is recorded as of its arrival, with the number placed once the evaluation reached the country`() {
    val release = CountDownLatch(1)

    screening(budgetMillis = 100) {
          release.await()
          null
        }
        .screen(ScreenedCaller.Presented(CallerNumber.parse("02079460123")), { DialingCountry.of("gb", null) }) {
          responses.add(it.reason)
        }

    assertEquals(ScreeningReason.TIMED_OUT, responses.poll(1, TimeUnit.SECONDS))
    release.countDown()
    assertEquals(Recorded("+442079460123", ScreeningDecision.ALLOW, arrival), recorded.poll(1, TimeUnit.SECONDS))
  }
}
