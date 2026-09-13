package org.letmehandle.app.calls.screening

import android.os.Build
import android.telecom.Call
import android.telecom.CallScreeningService
import android.telephony.TelephonyManager
import android.util.Log
import androidx.annotation.RequiresApi
import java.time.Instant
import java.util.concurrent.Executors
import org.letmehandle.app.calls.CallScreeningGraph
import org.letmehandle.app.calls.FailureSummary
import org.letmehandle.app.calls.rules.DialingCountry
import org.letmehandle.app.calls.rules.ScreeningDecision

/** Screens each incoming call from the stored rules while the app holds the call-screening role (D-028). */
@RequiresApi(Build.VERSION_CODES.Q)
class RulesScreeningService : CallScreeningService() {
  override fun onScreenCall(callDetails: Call.Details) {
    // Outgoing calls are passed here too, and a response to one is ignored.
    if (callDetails.callDirection != Call.Details.DIRECTION_INCOMING) {
      return
    }
    val graph = CallScreeningGraph.get(this)
    val screening =
        IncomingCallScreening(
            screener = SCREENER,
            clock = Instant::now,
            readSnapshot = graph::readSnapshot,
            record = graph.ledger::screened,
            onFailure = { failure ->
              Log.e(CallScreeningGraph.TAG, "screening a call failed: ${FailureSummary.of(failure)}")
            },
        )
    val caller =
        HandlePresentation.callerOf(callDetails.handlePresentation, callDetails.handle?.schemeSpecificPart)
    screening.screen(caller, ::dialingCountry) { result ->
      respondToCall(callDetails, responseFor(result.decision))
      Log.i(CallScreeningGraph.TAG, "screened: ${result.decision} (${result.reason})")
    }
  }

  /** The country the network is in, or the SIM's when the network has not said; null on neither. */
  private fun dialingCountry(): DialingCountry? {
    val telephony = getSystemService(TelephonyManager::class.java) ?: return null
    return DialingCountry.of(telephony.networkCountryIso, telephony.simCountryIso)
  }

  companion object {
    private val SCREENER =
        DeadlineScreener(worker = Executors.newSingleThreadExecutor(), timer = Executors.newSingleThreadScheduledExecutor())

    /** The platform response for a decision; the call log entry is never skipped. */
    fun responseFor(decision: ScreeningDecision): CallResponse =
        when (decision) {
          ScreeningDecision.ALLOW -> CallResponse.Builder().build()
          ScreeningDecision.SILENCE -> CallResponse.Builder().setSilenceCall(true).build()
          ScreeningDecision.REJECT ->
              CallResponse.Builder().setDisallowCall(true).setRejectCall(true).build()
        }
  }
}
