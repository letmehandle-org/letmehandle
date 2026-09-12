package org.letmehandle.app.calls.screening

import java.util.concurrent.Executor
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import org.letmehandle.app.calls.rules.Screening
import org.letmehandle.app.calls.rules.ScreeningDecision
import org.letmehandle.app.calls.rules.ScreeningReason

/**
 * A screening decision that always arrives within the budget, and arrives once.
 *
 * `onScreenCall` runs on the main thread, and the platform stops listening five seconds after it
 * is called. The evaluation itself is microseconds; reading the snapshot from storage is what
 * could stall, on a handset under memory pressure. So the evaluation runs on a worker, a timer
 * races it, and whichever finishes first is the answer. When the timer wins, the call rings: a
 * decision that is late is not applied by the platform anyway, and ringing is the one that can
 * never refuse somebody wrongly.
 */
class DeadlineScreener(
    private val worker: Executor,
    private val timer: ScheduledExecutorService,
    private val budgetMillis: Long = DEFAULT_BUDGET_MILLIS,
) {
  fun screen(
      evaluate: () -> Screening,
      onFailure: (RuntimeException) -> Unit,
      respond: (Screening) -> Unit,
  ) {
    val answered = AtomicBoolean(false)
    val once = { screening: Screening ->
      if (answered.compareAndSet(false, true)) {
        respond(screening)
      }
    }
    val deadline =
        timer.schedule(
            { once(Screening(ScreeningDecision.ALLOW, ScreeningReason.TIMED_OUT)) },
            budgetMillis,
            TimeUnit.MILLISECONDS,
        )
    worker.execute {
      val screening =
          try {
            evaluate()
          } catch (failure: RuntimeException) {
            onFailure(failure)
            Screening(ScreeningDecision.ALLOW, ScreeningReason.FAILED)
          }
      deadline.cancel(false)
      once(screening)
    }
  }

  companion object {
    /**
     * Three of the platform's five seconds: the rest is left for the response to travel back to
     * the telecom service, which happens after this budget and is not in this process's control.
     */
    const val DEFAULT_BUDGET_MILLIS = 3_000L
  }
}
