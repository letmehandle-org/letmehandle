package org.letmehandle.app.calls.events

import java.time.Instant
import org.json.JSONArray
import org.json.JSONException
import org.letmehandle.app.calls.rules.ScreeningDecision

/** Durable text by key, written before returning; a null value removes the key. */
interface TextStore {
  fun read(key: String): String?

  /** Applies every change in one atomic write, or none of them; throws [StoreUnavailable] when it cannot. */
  fun write(changes: Map<String, String?>)

  fun write(key: String, value: String?) = write(mapOf(key to value))
}

/** The handset's storage refused a write. */
class StoreUnavailable : IllegalStateException("call screening state could not be written")

/** The unreported call events and the tracked call, recorded under one lock only while an account is signed in (D-028). */
class CallEventLedger(
    private val store: TextStore,
    private val tracker: CallStateTracker,
    private val onChanged: () -> Unit,
    private val onOverflow: (dropped: Int) -> Unit,
    private val onUnreadable: (failure: Exception) -> Unit,
    private val capacity: Int = DEFAULT_CAPACITY,
) {
  private val lock = Any()

  /** Records calls from now on, for the account that has signed in. */
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

  /** Stops recording and forgets every event and the tracked call, with [alsoRemoving] in the same write. */
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
    return PENDING to CallEventRecord.listToJson(events.drop(dropped))
  }

  companion object {
    const val DEFAULT_CAPACITY = 500
    internal const val PENDING = "pending_call_events"
    internal const val TRACKED = "tracked_call"
    private const val RECORDING = "recording_calls"
    private const val RECORDING_ON = "on"
  }
}
