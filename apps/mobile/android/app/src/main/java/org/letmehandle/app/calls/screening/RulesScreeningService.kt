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
    // Read on the worker, because asking telephony is a call into another process and the main
    // thread is the one the platform is waiting on.
    val country = lazy { dialingCountry() }
    screener.screen(
        evaluate = { ScreeningRules.evaluate(graph.readSnapshot(), caller, Instant.now(), country.value) },
        onFailure = { failure ->
          Log.e(CallScreeningGraph.TAG, "screening a call failed: ${FailureSummary.of(failure)}")
        },
    ) { screening ->
      respondToCall(callDetails, responseFor(screening.decision))
      Log.i(CallScreeningGraph.TAG, "screened: ${screening.decision} (${screening.reason})")
      // Not asked for here when the evaluation never got as far: a response given on the deadline
      // must not wait on the same stalled call that made it late.
      val reached = if (country.isInitialized()) country.value else null
      val number = (caller as? ScreenedCaller.Presented)?.number?.e164(reached)
      graph.ledger.screened(number, screening.decision, Instant.now())
    }
  }

  /** The country the network is in, or the SIM's when the network has not said; null on neither. */
  private fun dialingCountry(): DialingCountry? {
    val telephony = getSystemService(TelephonyManager::class.java) ?: return null
    return DialingCountry.of(telephony.networkCountryIso, telephony.simCountryIso)
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
