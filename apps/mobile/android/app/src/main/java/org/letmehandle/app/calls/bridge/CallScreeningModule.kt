package org.letmehandle.app.calls.bridge

import android.app.Activity
import android.app.role.RoleManager
import android.content.Intent
import android.os.Build
import com.facebook.react.bridge.ActivityEventListener
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReadableArray
import java.util.concurrent.atomic.AtomicReference
import org.letmehandle.app.calls.CallScreeningGraph
import org.letmehandle.app.calls.events.CallEventRecord
import org.letmehandle.app.specs.NativeCallScreeningSpec

/** Call screening for the app's JavaScript, as `src/calls/native/NativeCallScreening.ts` declares it. */
class CallScreeningModule(private val context: ReactApplicationContext) :
    NativeCallScreeningSpec(context), ActivityEventListener {

  private val graph = CallScreeningGraph.get(context)
  private val pendingListener: () -> Unit = { emitOnCallEventsPending() }
  private val roleRequest = AtomicReference<Promise?>()

  init {
    context.addActivityEventListener(this)
    graph.addListener(pendingListener)
  }

  override fun invalidate() {
    graph.removeListener(pendingListener)
    context.removeActivityEventListener(this)
    roleRequest.getAndSet(null)?.resolve(STATUS_DECLINED)
    super.invalidate()
  }

  override fun roleStatus(promise: Promise) {
    promise.resolve(currentRoleStatus())
  }

  override fun requestRole(promise: Promise) {
    val status = currentRoleStatus()
    if (status != STATUS_AVAILABLE) {
      promise.resolve(status)
      return
    }
    val activity = context.currentActivity
    if (activity == null) {
      promise.reject("no_activity", "the role can only be requested from a visible screen")
      return
    }
    roleRequest.getAndSet(promise)?.resolve(STATUS_DECLINED)
    val roles = activity.getSystemService(RoleManager::class.java)
    activity.startActivityForResult(roles.createRequestRoleIntent(RoleManager.ROLE_CALL_SCREENING), REQUEST_ROLE)
  }

  override fun onActivityResult(activity: Activity, requestCode: Int, resultCode: Int, data: Intent?) {
    if (requestCode != REQUEST_ROLE) {
      return
    }
    // RESULT_CANCELED also covers a user who asked not to be asked again.
    roleRequest.getAndSet(null)?.resolve(if (resultCode == Activity.RESULT_OK) STATUS_HELD else STATUS_DECLINED)
  }

  override fun onNewIntent(intent: Intent) = Unit

  override fun writeRulesSnapshot(snapshot: String, promise: Promise) = promise.settle { graph.writeSnapshot(snapshot) }

  override fun startRecordingCalls(promise: Promise) = promise.settle { graph.rememberAccount() }

  override fun forgetAccount(promise: Promise) = promise.settle { graph.forgetAccount() }

  override fun pendingCallEvents(promise: Promise) =
      promise.settle { CallEventRecord.listToJson(graph.ledger.pending()) }

  override fun acknowledgeCallEvents(eventIds: ReadableArray, promise: Promise) =
      promise.settle { graph.ledger.acknowledge((0 until eventIds.size()).mapNotNull(eventIds::getString)) }

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
