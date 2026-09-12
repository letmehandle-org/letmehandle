package org.letmehandle.app.calls

import android.content.Context
import android.content.SharedPreferences
import android.util.Log
import java.util.UUID
import java.util.concurrent.CopyOnWriteArraySet
import org.letmehandle.app.calls.events.CallEventLedger
import org.letmehandle.app.calls.events.CallStateTracker
import org.letmehandle.app.calls.events.TextStore
import org.letmehandle.app.calls.rules.CallRulesSnapshot
import org.letmehandle.app.calls.rules.CallRulesSnapshotCodec

/**
 * The pieces the screening service, the phone-state receiver and the React Native bridge share.
 *
 * One per process. Each of those three can be the first thing a process starts for — a call can
 * arrive while the app has never been opened since boot — so none of them can own the wiring.
 */
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
      )

  /** The rules as last written by the app, or null when there are none it can read. */
  fun readSnapshot(): CallRulesSnapshot? {
    val text = store.read(SNAPSHOT) ?: return null
    return try {
      CallRulesSnapshotCodec.decode(text)
    } catch (invalid: CallRulesSnapshotCodec.InvalidSnapshot) {
      // Refused rather than half-read, and said: the call will ring as if there were no rules.
      Log.w(TAG, "the stored call rules could not be read", invalid)
      null
    }
  }

  /**
   * Validates before storing, so a document the service cannot read is refused to the app.
   *
   * A refused document also removes the one stored before it. The app only writes when the
   * rules changed, so the older copy is known to be out of date — a newer format this build does
   * not understand, say — and the service must fall back to letting calls ring rather than keep
   * refusing callers on rules the user has since replaced.
   */
  fun writeSnapshot(text: String) {
    try {
      CallRulesSnapshotCodec.decode(text)
    } catch (invalid: CallRulesSnapshotCodec.InvalidSnapshot) {
      store.write(SNAPSHOT, null)
      throw invalid
    }
    store.write(SNAPSHOT, text)
  }

  /** Forget the account's rules and its unreported calls, for a sign-out. */
  fun forgetAccount() {
    store.write(SNAPSHOT, null)
    ledger.clear()
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

    override fun write(key: String, value: String?) {
      val editor = preferences.edit()
      if (value == null) editor.remove(key) else editor.putString(key, value)
      check(editor.commit()) { "call screening state could not be written" }
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
