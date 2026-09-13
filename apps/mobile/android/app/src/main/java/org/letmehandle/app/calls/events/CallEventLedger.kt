package org.letmehandle.app.calls.events

import java.time.Instant
import org.json.JSONArray
import org.json.JSONException
import org.letmehandle.app.calls.rules.ScreeningDecision

/** Durable text by key, written before returning; a null value removes the key. */
interface TextStore {
  fun read(key: String): String?

  /** Applies every change in one atomic write, or none of them. */
  fun write(changes: Map<String, String?>)

  fun write(key: String, value: String?) = write(mapOf(key to value))
}

/**
 * The handset's record of call events not yet acknowledged by the backend, and the call being
 * followed.
 *
 * Records only while an account is signed in: from [startRecording] until [clear]. A call that
 * arrives with nobody signed in is nobody's to report, and on a shared phone it is somebody
 * else's; kept, it would be reported as the calls of whoever signed in next. The switch is stored
 * beside the events and read under the same lock, so a sign-out can never be followed by one
 * more event recorded for the account that left.
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

  /** Record calls from now on, for the account that has signed in. */
  fun startRecording() {
    synchronized(lock) { store.write(RECORDING, RECORDING_ON) }
  }

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

  /**
   * Forget everything and stop recording, for a sign-out: these calls belong to the account that
   * is leaving, and the next ones to nobody until another signs in.
   */
  fun clear(alsoRemoving: Collection<String> = emptyList()) {
    synchronized(lock) {
      store.write((listOf(RECORDING, PENDING, TRACKED) + alsoRemoving).associateWith { null })
    }
  }

  private fun apply(step: (TrackedCall?) -> Transition) {
    val changed =
        synchronized(lock) {
          if (store.read(RECORDING) != RECORDING_ON) {
            return
          }
          val transition = step(readTracked())
          val changes = mutableMapOf<String, String?>(TRACKED to transition.call?.toJson()?.toString())
          if (transition.events.isNotEmpty()) {
            changes += pendingChange(readPending() + transition.events)
          }
          store.write(changes)
          transition.events.isNotEmpty()
        }
    if (changed) {
      onChanged()
    }
  }

  /** The call being followed, forgotten and reported when what is stored cannot be read. */
  private fun readTracked(): TrackedCall? {
    val text = store.read(TRACKED) ?: return null
    return try {
      TrackedCall.fromJson(text)
    } catch (unreadable: UnreadableRecord) {
      store.write(TRACKED, null)
      onUnreadable(unreadable)
      null
    }
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
            CallEventRecord.fromJson(entries.optJSONObject(index) ?: throw UnreadableRecord(null))
          } catch (unreadable: UnreadableRecord) {
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
    store.write(mapOf(pendingChange(events)))
  }

  private fun pendingChange(events: List<CallEventRecord>): Pair<String, String> {
    val dropped = (events.size - capacity).coerceAtLeast(0)
    if (dropped > 0) {
      onOverflow(dropped)
    }
    return PENDING to JSONArray(events.drop(dropped).map { it.toJson() }).toString()
  }

  companion object {
    const val DEFAULT_CAPACITY = 500
    internal const val PENDING = "pending_call_events"
    internal const val TRACKED = "tracked_call"
    private const val RECORDING = "recording_calls"
    private const val RECORDING_ON = "on"
  }
}
