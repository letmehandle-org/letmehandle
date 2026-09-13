package org.letmehandle.app.calls.screening

import java.time.Instant
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.letmehandle.app.calls.rules.CallerNumber
import org.letmehandle.app.calls.rules.ScreenedCaller
import org.letmehandle.app.calls.rules.Screening
import org.letmehandle.app.calls.rules.ScreeningDecision
import org.letmehandle.app.calls.rules.ScreeningReason
import org.letmehandle.app.calls.rules.ScreeningRules

/**
 * Real threads and a real timer, on purpose: what is being proven is that a response arrives
 * within the budget and arrives once, and a fake clock would prove only that the code asks it.
 */
class DeadlineScreenerTest {
  private val worker = Executors.newSingleThreadExecutor()
  private val timer = Executors.newSingleThreadScheduledExecutor()
  private val responses = LinkedBlockingQueue<Screening>()
  private val failures = LinkedBlockingQueue<RuntimeException>()

  @After
  fun stop() {
    worker.shutdownNow()
    timer.shutdownNow()
  }

  private fun screener(budgetMillis: Long) = DeadlineScreener(worker, timer, budgetMillis)

  @Test
  fun `a decision made in time is the one given`() {
    val rejected = Screening(ScreeningDecision.REJECT, ScreeningReason.DEFAULT_POSTURE)
    val started = System.nanoTime()

    screener(budgetMillis = 1_000).screen({ rejected }, failures::add, responses::add)

    assertEquals(rejected, responses.poll(1, TimeUnit.SECONDS))
    assertTrue(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started) < 500)
    assertNull("a decision is given once", responses.poll(1_200, TimeUnit.MILLISECONDS))
  }

  @Test
  fun `a decision that is not ready in time lets the call ring, within the budget`() {
    val release = CountDownLatch(1)
    val started = System.nanoTime()

    screener(budgetMillis = 200).screen(
        {
          release.await()
          Screening(ScreeningDecision.REJECT, ScreeningReason.DEFAULT_POSTURE)
        },
        failures::add,
        responses::add,
    )

    assertEquals(Screening(ScreeningDecision.ALLOW, ScreeningReason.TIMED_OUT), responses.poll(1, TimeUnit.SECONDS))
    val elapsed = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started)
    assertTrue("answered after ${elapsed}ms", elapsed in 150..1_000)

    release.countDown()
    assertNull("the late decision is not given as well", responses.poll(300, TimeUnit.MILLISECONDS))
  }

  @Test
  fun `a failed evaluation lets the call ring and is reported`() {
    val broken = IllegalStateException("storage unavailable")

    screener(budgetMillis = 1_000).screen({ throw broken }, failures::add, responses::add)

    assertEquals(Screening(ScreeningDecision.ALLOW, ScreeningReason.FAILED), responses.poll(1, TimeUnit.SECONDS))
    assertEquals(broken, failures.poll(1, TimeUnit.SECONDS))
  }

  @Test
  fun `a response that fails is reported, and does not escape onto the worker`() {
    // What recording a decision does when shared preferences cannot commit. On a handset an
    // exception that reaches a thread's top ends the whole process.
    val unwritable = IllegalStateException("call screening state could not be written")
    val escaped = LinkedBlockingQueue<Throwable>()
    val worker =
        Executors.newSingleThreadExecutor { task ->
          Thread(task).apply { setUncaughtExceptionHandler { _, failure -> escaped.add(failure) } }
        }

    try {
      DeadlineScreener(worker, timer, budgetMillis = 1_000).screen(
          { Screening(ScreeningDecision.ALLOW, ScreeningReason.DEFAULT_POSTURE) },
          failures::add,
      ) { throw unwritable }

      assertEquals(unwritable, failures.poll(1, TimeUnit.SECONDS))
      assertNull(escaped.poll(200, TimeUnit.MILLISECONDS))
    } finally {
      worker.shutdownNow()
    }
  }

  @Test
  fun `a response that fails after the deadline is reported rather than lost in the timer`() {
    val release = CountDownLatch(1)
    val unwritable = IllegalStateException("call screening state could not be written")
    // A scheduled task's exception is kept in its future, which nothing reads.
    val timer = Executors.newSingleThreadScheduledExecutor()

    try {
      DeadlineScreener(worker, timer, budgetMillis = 100).screen(
          {
            release.await()
            Screening(ScreeningDecision.REJECT, ScreeningReason.DEFAULT_POSTURE)
          },
          failures::add,
      ) { throw unwritable }

      assertEquals(unwritable, failures.poll(1, TimeUnit.SECONDS))
    } finally {
      release.countDown()
      timer.shutdownNow()
    }
  }

  @Test
  fun `a handset that has never synced its rules lets the call ring, in time`() {
    val caller = ScreenedCaller.Presented(CallerNumber.parse("+12025550145"))

    DeadlineScreener(worker, timer).screen(
        { ScreeningRules.evaluate(snapshot = null, caller = caller, now = Instant.now(), country = null) },
        failures::add,
        responses::add,
    )

    assertEquals(
        Screening(ScreeningDecision.ALLOW, ScreeningReason.NO_RULES),
        responses.poll(DeadlineScreener.DEFAULT_BUDGET_MILLIS, TimeUnit.MILLISECONDS),
    )
  }

  @Test
  fun `the default budget leaves room inside the platform's five seconds`() {
    assertTrue(DeadlineScreener.DEFAULT_BUDGET_MILLIS < 5_000)
  }
}
