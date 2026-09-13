package org.letmehandle.app.calls.screening

import java.time.Instant
import org.letmehandle.app.calls.rules.CallRulesSnapshot
import org.letmehandle.app.calls.rules.DialingCountry
import org.letmehandle.app.calls.rules.ScreenedCaller
import org.letmehandle.app.calls.rules.Screening
import org.letmehandle.app.calls.rules.ScreeningDecision
import org.letmehandle.app.calls.rules.ScreeningRules

/** Screens one incoming call against the stored rules within the deadline, then records it as of its arrival. */
class IncomingCallScreening(
    private val screener: DeadlineScreener,
    private val clock: () -> Instant,
    private val readSnapshot: () -> CallRulesSnapshot?,
    private val record: (callerNumber: String?, decision: ScreeningDecision, occurredAt: Instant) -> Unit,
    private val onFailure: (RuntimeException) -> Unit,
) {
  fun screen(caller: ScreenedCaller, dialingCountry: () -> DialingCountry?, respond: (Screening) -> Unit) {
    val arrivedAt = clock()
    val country = lazy(dialingCountry)
    screener.screen(
        evaluate = { ScreeningRules.evaluate(readSnapshot(), caller, arrivedAt, country.value) },
        onFailure = onFailure,
        respond = respond,
        record = { screening ->
          // The country is only asked for by the evaluation, never by a recording that could stall with it.
          val reached = if (country.isInitialized()) country.value else null
          record((caller as? ScreenedCaller.Presented)?.number?.e164(reached), screening.decision, arrivedAt)
        },
    )
  }
}
