package org.letmehandle.app.calls.screening

import java.util.concurrent.Executor
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import org.letmehandle.app.calls.rules.Screening
import org.letmehandle.app.calls.rules.ScreeningDecision
import org.letmehandle.app.calls.rules.ScreeningReason

/** Races the evaluation on [worker] against a [timer], responds once, and records the answer on [worker]. */
class DeadlineScreener(
    private val worker: Executor,
    private val timer: ScheduledExecutorService,
    private val budgetMillis: Long = DEFAULT_BUDGET_MILLIS,
) {
  fun screen(
      evaluate: () -> Screening,
      onFailure: (RuntimeException) -> Unit,
      respond: (Screening) -> Unit,
      record: (Screening) -> Unit,
  ) {
    val answered = AtomicBoolean(false)
    val reporting = { step: () -> Unit ->
      try {
        step()
      } catch (failure: RuntimeException) {
        onFailure(failure)
      }
    }
    val deadline =
        timer.schedule(
            {
              val timedOut = Screening(ScreeningDecision.ALLOW, ScreeningReason.TIMED_OUT)
              if (answered.compareAndSet(false, true)) {
                reporting { respond(timedOut) }
                worker.execute { reporting { record(timedOut) } }
              }
            },
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
      if (answered.compareAndSet(false, true)) {
        reporting { respond(screening) }
        reporting { record(screening) }
      }
    }
  }

  companion object {
    /** Three of the platform's five seconds, leaving the rest for the response to reach telecom. */
    const val DEFAULT_BUDGET_MILLIS = 3_000L
  }
}
