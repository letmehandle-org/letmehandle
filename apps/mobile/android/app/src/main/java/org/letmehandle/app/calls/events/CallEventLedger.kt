package org.letmehandle.app.calls.events

import java.time.Instant
import java.time.format.DateTimeParseException
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import org.letmehandle.app.calls.rules.ScreeningDecision

/** Durable text, by key. Backed by shared preferences on a handset and by a map in tests. */
interface TextStore {
  fun read(key: String): String?

  /** Writes before returning. A broadcast receiver's process may be gone a moment later. */
  fun write(key: String, value: String?)
}

/**
 * The handset's record of call events not yet acknowledged by the backend, and the call being
 * followed.
 *
 * Written from the screening service and the phone-state receiver, read and emptied by the app.
 * Those can run on different threads, so every operation holds the one lock.
 *
 * Bounded: a handset whose app is never opened would otherwise grow this without end. The
 * oldest events go first, and [onOverflow] is told how many, so the loss is visible.
 *
 * Forgiving on read: an entry that cannot be read is dropped, [onUnreadable] is told why, and the
 * ledger is written back without it. Refusing the whole list instead would hold every call behind
 * that one entry on the handset for good, and fail every read after.
 */
class CallEventLedger(
    private val store: TextStore,
    private val tracker: CallStateTracker,
    private val onChanged: () -> Unit,
    private val onOverflow: (dropped: Int) -> Unit,
    private val onUnreadable: (failure: Exception) -> Unit,
    private val capacity: Int = DEFAULT_CAPACITY,
) {
  private val lock = Any()

  fun screened(callerNumber: String?, decision: ScreeningDecision, now: Instant) =
      apply { current -> tracker.screened(current, callerNumber, decision, now) }

  fun phoneState(state: PhoneState, now: Instant) =
      apply { current -> tracker.phoneState(current, state, now) }

  fun pending(): List<CallEventRecord> = synchronized(lock) { readPending() }

  fun acknowledge(eventIds: Collection<String>) {
    synchronized(lock) {
      val remaining = readPending().filterNot { it.eventId in eventIds }
      writePending(remaining)
    }
  }

  /** Forget everything, for a sign-out: these calls belong to the account that is leaving. */
  fun clear() {
    synchronized(lock) {
      store.write(PENDING, null)
      store.write(TRACKED, null)
    }
  }

  private fun apply(step: (TrackedCall?) -> Transition) {
    val changed =
        synchronized(lock) {
          val tracked = readTracked()
          val transition = step(tracked)
          store.write(TRACKED, transition.call?.toJson()?.toString())
          if (transition.events.isNotEmpty()) {
            writePending(readPending() + transition.events)
          }
          transition.events.isNotEmpty()
        }
    if (changed) {
      onChanged()
    }
  }

  /**
   * The call being followed, or none if what is stored cannot be read.
   *
   * Forgotten rather than thrown: a corrupt record here would otherwise stop every later screening
   * decision and phone state from being recorded at all. The next event starts from nothing, which
   * at worst reports one call as two.
   */
  private fun readTracked(): TrackedCall? {
    val text = store.read(TRACKED) ?: return null
    return try {
      TrackedCall.fromJson(JSONObject(text))
    } catch (unreadable: JSONException) {
      forgetTracked(unreadable)
    } catch (unreadable: DateTimeParseException) {
      forgetTracked(unreadable)
    }
  }

  private fun forgetTracked(failure: Exception): TrackedCall? {
    store.write(TRACKED, null)
    onUnreadable(failure)
    return null
  }

  private fun readPending(): List<CallEventRecord> {
    val text = store.read(PENDING) ?: return emptyList()
    val entries =
        try {
          JSONArray(text)
        } catch (unreadable: JSONException) {
          store.write(PENDING, null)
          onUnreadable(unreadable)
          return emptyList()
        }
    val events =
        (0 until entries.length()).mapNotNull { index ->
          try {
            CallEventRecord.fromJson(entries.optJSONObject(index) ?: throw CallEventRecord.UnreadableRecord(null))
          } catch (unreadable: CallEventRecord.UnreadableRecord) {
            onUnreadable(unreadable)
            null
          }
        }
    if (events.size < entries.length()) {
      writePending(events)
    }
    return events
  }

  private fun writePending(events: List<CallEventRecord>) {
    val dropped = (events.size - capacity).coerceAtLeast(0)
    if (dropped > 0) {
      onOverflow(dropped)
    }
    store.write(PENDING, JSONArray(events.drop(dropped).map { it.toJson() }).toString())
  }

  companion object {
    const val DEFAULT_CAPACITY = 500
    internal const val PENDING = "pending_call_events"
    internal const val TRACKED = "tracked_call"
  }
}
