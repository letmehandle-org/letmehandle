package org.letmehandle.app.calls

import android.content.Context
import android.content.SharedPreferences
import android.util.Log
import java.util.UUID
import java.util.concurrent.CopyOnWriteArraySet
import org.letmehandle.app.calls.events.CallEventLedger
import org.letmehandle.app.calls.events.CallStateTracker
import org.letmehandle.app.calls.events.StoreUnavailable
import org.letmehandle.app.calls.events.TextStore
import org.letmehandle.app.calls.rules.CallRulesSnapshot
import org.letmehandle.app.calls.rules.CallRulesSnapshotCodec

/** The per-process wiring shared by the screening service, the phone-state receiver and the bridge. */
class CallScreeningGraph private constructor(context: Context) {
  private val preferences: SharedPreferences =
      context.applicationContext.getSharedPreferences(STORE_NAME, Context.MODE_PRIVATE)
  private val store = SharedPreferencesTextStore(preferences)
  private val listeners = CopyOnWriteArraySet<() -> Unit>()

  val ledger =
      CallEventLedger(
          store = store,
          tracker = CallStateTracker { UUID.randomUUID().toString() },
          onChanged = { listeners.forEach { it() } },
          onOverflow = { dropped ->
            Log.w(TAG, "dropped $dropped unreported call events to stay within capacity")
          },
          onUnreadable = { failure ->
            Log.w(TAG, "dropped an unreadable unreported call event: ${FailureSummary.of(failure)}")
          },
      )

  /** The rules as last written by the app, or null when there are none it can read. */
  fun readSnapshot(): CallRulesSnapshot? {
    val text = store.read(SNAPSHOT) ?: return null
    return try {
      CallRulesSnapshotCodec.decode(text)
    } catch (invalid: CallRulesSnapshotCodec.InvalidSnapshot) {
      // An unreadable snapshot is logged and read as none, so the call rings.
      Log.w(TAG, "the stored call rules could not be read: ${FailureSummary.of(invalid)}")
      null
    }
  }

  /** Stores a readable snapshot, or removes the stored one and throws for an unreadable one. */
  fun writeSnapshot(text: String) {
    try {
      CallRulesSnapshotCodec.decode(text)
    } catch (invalid: CallRulesSnapshotCodec.InvalidSnapshot) {
      store.write(SNAPSHOT, null)
      throw invalid
    }
    store.write(SNAPSHOT, text)
  }

  /** Starts recording calls for the account that has signed in. */
  fun rememberAccount() {
    ledger.startRecording()
  }

  /** Forgets the account's rules and unreported calls and stops recording, in one write. */
  fun forgetAccount() {
    ledger.clear(alsoRemoving = listOf(SNAPSHOT))
  }

  fun addListener(listener: () -> Unit) {
    listeners.add(listener)
  }

  fun removeListener(listener: () -> Unit) {
    listeners.remove(listener)
  }

  private class SharedPreferencesTextStore(private val preferences: SharedPreferences) :
      TextStore {
    override fun read(key: String): String? = preferences.getString(key, null)

    override fun write(changes: Map<String, String?>) {
      val editor = preferences.edit()
      changes.forEach { (key, value) -> if (value == null) editor.remove(key) else editor.putString(key, value) }
      if (!editor.commit()) throw StoreUnavailable()
    }
  }

  companion object {
    const val TAG = "LetMeHandleScreening"
    private const val STORE_NAME = "letmehandle.call_screening"
    private const val SNAPSHOT = "call_rules_snapshot"

    @Volatile private var instance: CallScreeningGraph? = null

    fun get(context: Context): CallScreeningGraph =
        instance
            ?: synchronized(this) {
              instance ?: CallScreeningGraph(context).also { instance = it }
            }
  }
}
