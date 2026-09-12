package org.letmehandle.app.calls.screening

import android.os.Build
import android.telecom.Call
import android.telecom.CallScreeningService
import android.util.Log
import androidx.annotation.RequiresApi
import java.time.Instant
import java.util.concurrent.Executors
import org.letmehandle.app.calls.CallScreeningGraph
import org.letmehandle.app.calls.FailureSummary
import org.letmehandle.app.calls.rules.ScreenedCaller
import org.letmehandle.app.calls.rules.ScreeningDecision
import org.letmehandle.app.calls.rules.ScreeningRules

/**
 * Decides each incoming call before the handset rings, from the user's own rules.
 *
 * Bound by the platform only while this app holds the call-screening role, which the user grants.
 * It is told the caller's number and presentation and nothing else — never the call's audio —
 * and it must answer within five seconds.
 *
 * Enabled from API 29, where the role exists and a ringing call can be silenced. Below that the
 * platform binds a screening service only for the phone app, which this is not.
 */
@RequiresApi(Build.VERSION_CODES.Q)
class RulesScreeningService : CallScreeningService() {
  private val screener = DeadlineScreener(worker = WORKER, timer = TIMER)

  override fun onScreenCall(callDetails: Call.Details) {
    // Outgoing calls are passed here too; a response to one is ignored, so none is given.
    if (callDetails.callDirection != Call.Details.DIRECTION_INCOMING) {
      return
    }
    val graph = CallScreeningGraph.get(this)
    val caller =
        HandlePresentation.callerOf(callDetails.handlePresentation, callDetails.handle?.schemeSpecificPart)
    screener.screen(
        evaluate = { ScreeningRules.evaluate(graph.readSnapshot(), caller, Instant.now()) },
        onFailure = { failure ->
          Log.e(CallScreeningGraph.TAG, "screening a call failed: ${FailureSummary.of(failure)}")
        },
    ) { screening ->
      respondToCall(callDetails, responseFor(screening.decision))
      Log.i(CallScreeningGraph.TAG, "screened: ${screening.decision} (${screening.reason})")
      graph.ledger.screened((caller as? ScreenedCaller.Presented)?.number?.e164, screening.decision, Instant.now())
    }
  }

  companion object {
    private val WORKER = Executors.newSingleThreadExecutor()
    private val TIMER = Executors.newSingleThreadScheduledExecutor()

    /**
     * The platform's response for each decision.
     *
     * Reject disallows and rejects, so the caller is refused as if the user had declined; the call
     * is logged by the platform as blocked. Silence lets the call through without a ringtone. Allow
     * sets nothing. Skipping the call log is not attempted: the platform honours it only for
     * carrier and system screening apps, and a user deserves to see what was refused anyway.
     */
    fun responseFor(decision: ScreeningDecision): CallResponse =
        when (decision) {
          ScreeningDecision.ALLOW -> CallResponse.Builder().build()
          ScreeningDecision.SILENCE -> CallResponse.Builder().setSilenceCall(true).build()
          ScreeningDecision.REJECT ->
              CallResponse.Builder().setDisallowCall(true).setRejectCall(true).build()
        }
  }
}
