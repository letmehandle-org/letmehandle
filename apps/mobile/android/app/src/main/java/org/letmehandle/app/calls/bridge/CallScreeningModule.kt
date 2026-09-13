package org.letmehandle.app.calls.bridge

import android.app.Activity
import android.app.role.RoleManager
import android.content.Intent
import android.os.Build
import com.facebook.react.bridge.ActivityEventListener
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReadableArray
import org.json.JSONArray
import org.letmehandle.app.calls.CallScreeningGraph
import org.letmehandle.app.calls.rules.CallRulesSnapshotCodec
import org.letmehandle.app.specs.NativeCallScreeningSpec

/**
 * Call screening, for the app's JavaScript.
 *
 * Every value that crosses is named in `src/calls/native/NativeCallScreening.ts`; the role
 * statuses here are the `RoleStatus` and `RoleRequestOutcome` unions in `src/calls/screeningRole.ts`.
 */
class CallScreeningModule(private val context: ReactApplicationContext) :
    NativeCallScreeningSpec(context), ActivityEventListener {

  private val graph = CallScreeningGraph.get(context)
  private val pendingListener: () -> Unit = { emitOnCallEventsPending() }
  private var roleRequest: Promise? = null

  init {
    context.addActivityEventListener(this)
    graph.addListener(pendingListener)
  }

  override fun invalidate() {
    graph.removeListener(pendingListener)
    context.removeActivityEventListener(this)
    super.invalidate()
  }

  override fun roleStatus(promise: Promise) {
    promise.resolve(currentRoleStatus())
  }

  override fun requestRole(promise: Promise) {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
      promise.resolve(STATUS_UNAVAILABLE)
      return
    }
    val activity = context.currentActivity
    if (activity == null) {
      // Asked from a screen that is no longer in front: there is nothing to show the prompt on.
      promise.reject("no_activity", "the role can only be requested from a visible screen")
      return
    }
    val roles = activity.getSystemService(RoleManager::class.java)
    when {
      !roles.isRoleAvailable(RoleManager.ROLE_CALL_SCREENING) -> promise.resolve(STATUS_UNAVAILABLE)
      roles.isRoleHeld(RoleManager.ROLE_CALL_SCREENING) -> promise.resolve(STATUS_HELD)
      else -> {
        roleRequest?.resolve(STATUS_DECLINED)
        roleRequest = promise
        activity.startActivityForResult(
            roles.createRequestRoleIntent(RoleManager.ROLE_CALL_SCREENING),
            REQUEST_ROLE,
        )
      }
    }
  }

  override fun onActivityResult(activity: Activity, requestCode: Int, resultCode: Int, data: Intent?) {
    if (requestCode != REQUEST_ROLE) {
      return
    }
    // RoleManager.createRequestRoleIntent: RESULT_OK when granted, RESULT_CANCELED otherwise —
    // including when the user has asked not to be asked again, which is indistinguishable here.
    roleRequest?.resolve(if (resultCode == Activity.RESULT_OK) STATUS_HELD else STATUS_DECLINED)
    roleRequest = null
  }

  override fun onNewIntent(intent: Intent) = Unit

  override fun writeRulesSnapshot(snapshot: String, promise: Promise) {
    try {
      graph.writeSnapshot(snapshot)
      promise.resolve(null)
    } catch (invalid: CallRulesSnapshotCodec.InvalidSnapshot) {
      promise.reject("invalid_snapshot", invalid.message, invalid)
    }
  }

  override fun forgetAccount(promise: Promise) {
    graph.forgetAccount()
    promise.resolve(null)
  }

  override fun pendingCallEvents(promise: Promise) {
    promise.resolve(JSONArray(graph.ledger.pending().map { it.toJson() }).toString())
  }

  override fun acknowledgeCallEvents(eventIds: ReadableArray, promise: Promise) {
    graph.ledger.acknowledge((0 until eventIds.size()).mapNotNull(eventIds::getString))
    promise.resolve(null)
  }

  private fun currentRoleStatus(): String {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
      return STATUS_UNAVAILABLE
    }
    val roles = context.getSystemService(RoleManager::class.java)
    return when {
      !roles.isRoleAvailable(RoleManager.ROLE_CALL_SCREENING) -> STATUS_UNAVAILABLE
      roles.isRoleHeld(RoleManager.ROLE_CALL_SCREENING) -> STATUS_HELD
      else -> STATUS_AVAILABLE
    }
  }

  companion object {
    private const val REQUEST_ROLE = 7_301
    private const val STATUS_HELD = "held"
    private const val STATUS_AVAILABLE = "available"
    private const val STATUS_UNAVAILABLE = "unavailable"
    private const val STATUS_DECLINED = "declined"
  }
}
