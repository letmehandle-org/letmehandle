package org.letmehandle.app.calls.events

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.telephony.TelephonyManager
import android.util.Log
import java.time.Instant
import org.letmehandle.app.calls.CallScreeningGraph
import org.letmehandle.app.calls.FailureSummary

/**
 * The handset's call state, without being the phone app.
 *
 * Declared in the manifest because the broadcast must reach a process that is not running: the
 * phone-state broadcast is one of the implicit broadcasts Android still delivers to manifest
 * receivers, and it needs only the READ_PHONE_STATE permission. It carries no identifier and,
 * without the call-log permission this app does not ask for, no number.
 */
class PhoneStateReceiver : BroadcastReceiver() {
  override fun onReceive(context: Context, intent: Intent) {
    if (intent.action != TelephonyManager.ACTION_PHONE_STATE_CHANGED) {
      return
    }
    val state = stateOf(intent.getStringExtra(TelephonyManager.EXTRA_STATE)) ?: return
    try {
      CallScreeningGraph.get(context).ledger.phoneState(state, Instant.now())
    } catch (unavailable: StoreUnavailable) {
      Log.e(CallScreeningGraph.TAG, "a phone state could not be recorded: ${FailureSummary.of(unavailable)}")
    }
  }

  companion object {
    fun stateOf(extra: String?): PhoneState? =
        when (extra) {
          TelephonyManager.EXTRA_STATE_RINGING -> PhoneState.RINGING
          TelephonyManager.EXTRA_STATE_OFFHOOK -> PhoneState.OFFHOOK
          TelephonyManager.EXTRA_STATE_IDLE -> PhoneState.IDLE
          else -> null
        }
  }
}
