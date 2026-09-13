package org.letmehandle.app.calls.events

import java.time.Duration
import java.time.Instant
import org.json.JSONObject
import org.letmehandle.app.calls.rules.ScreeningDecision

/** The handset's telephony call state, as `TelephonyManager` reports it. */
enum class PhoneState {
  IDLE,
  RINGING,
  OFFHOOK,
}

/**
 * The call being followed, if there is one. Persisted between broadcasts, because the process
 * that received "ringing" is not necessarily alive to receive "idle".
 */
data class TrackedCall(
    val callId: String,
    val screenedAt: Instant?,
    val ringing: Boolean,
    val answered: Boolean,
) {
  fun toJson(): JSONObject =
      JSONObject().apply {
        put("call_id", callId)
        screenedAt?.let { put("screened_at", it.toString()) }
        put("ringing", ringing)
        put("answered", answered)
      }

  companion object {
    fun fromJson(json: JSONObject): TrackedCall =
        TrackedCall(
            callId = json.getString("call_id"),
            screenedAt =
                if (json.has("screened_at")) Instant.parse(json.getString("screened_at")) else null,
            ringing = json.getBoolean("ringing"),
            answered = json.getBoolean("answered"),
        )
  }
}

data class Transition(val call: TrackedCall?, val events: List<CallEventRecord>)

/**
 * Screening decisions and telephony call states, turned into incoming, answered and ended.
 *
 * Without the phone app's role, an application sees two things: the screening service's call
 * (with a number, before ringing, and only for callers not in the user's contacts and not
 * withheld), and the telephony call state broadcast (ringing, off hook, idle — no number, no
 * identifier). This joins them. A screened call that then rings is the same call; a call that
 * rings without having been screened is a new one, reported without a number or a decision.
 *
 * What it cannot see, and therefore does not report: a second call waiting behind one already
 * in progress, whose state never reaches "ringing" in the broadcast; and anything at all when the
 * phone-state permission is refused, beyond the screening decision itself.
 *
 * Pure. Identifiers and the clock are passed in, so every sequence is testable on the JVM.
 */
class CallStateTracker(private val newId: () -> String) {

  fun screened(
      current: TrackedCall?,
      callerNumber: String?,
      decision: ScreeningDecision,
      now: Instant,
  ): Transition {
    val callId = newId()
    val incoming =
        CallEventRecord(
            eventId = newId(),
            callId = callId,
            kind = CallEventKind.INCOMING,
            occurredAt = now,
            callerNumber = callerNumber,
            screening = decision,
        )
    if (decision == ScreeningDecision.REJECT) {
      // Refused before it rang: it never reaches the telephony state, so it ends here.
      val ended =
          CallEventRecord(
              eventId = newId(),
              callId = callId,
              kind = CallEventKind.ENDED,
              occurredAt = now,
              ending = CallEnding.SCREENED_OUT,
          )
      return Transition(current, listOf(incoming, ended))
    }
    val following = current?.takeIf { it.ringing || it.answered }
    return Transition(
        following ?: TrackedCall(callId, screenedAt = now, ringing = false, answered = false),
        listOf(incoming),
    )
  }

  fun phoneState(current: TrackedCall?, state: PhoneState, now: Instant): Transition =
      when (state) {
        PhoneState.RINGING -> ringing(current, now)
        PhoneState.OFFHOOK -> offHook(current, now)
        PhoneState.IDLE -> idle(current, now)
      }

  private fun ringing(current: TrackedCall?, now: Instant): Transition {
    if (current != null && (current.ringing || current.answered)) {
      return Transition(current, emptyList())
    }
    val screenedAt = current?.screenedAt
    if (current != null && screenedAt != null && Duration.between(screenedAt, now) <= SCREENED_WINDOW) {
      return Transition(current.copy(ringing = true), emptyList())
    }
    val callId = newId()
    return Transition(
        TrackedCall(callId, screenedAt = null, ringing = true, answered = false),
        listOf(CallEventRecord(newId(), callId, CallEventKind.INCOMING, now)),
    )
  }

  private fun offHook(current: TrackedCall?, now: Instant): Transition {
    // Off hook with nothing ringing is the user placing a call, which is not this product's.
    if (current == null || !current.ringing || current.answered) {
      return Transition(current?.takeIf { it.ringing || it.answered }, emptyList())
    }
    return Transition(
        current.copy(answered = true),
        listOf(CallEventRecord(newId(), current.callId, CallEventKind.ANSWERED, now)),
    )
  }

  private fun idle(current: TrackedCall?, now: Instant): Transition {
    if (current == null || !(current.ringing || current.answered)) {
      // A screened call that never rang is left for its window, in case the broadcast is late.
      return Transition(current?.takeIf { it.screenedAt != null && !isExpired(it, now) }, emptyList())
    }
    val ending = if (current.answered) CallEnding.COMPLETED else CallEnding.MISSED
    return Transition(
        null,
        listOf(
            CallEventRecord(newId(), current.callId, CallEventKind.ENDED, now, ending = ending)
        ),
    )
  }

  private fun isExpired(call: TrackedCall, now: Instant): Boolean =
      call.screenedAt?.let { Duration.between(it, now) > SCREENED_WINDOW } ?: true

  companion object {
    /**
     * How long after a screening decision a "ringing" broadcast is taken to be that call. The
     * platform rings as soon as the decision is received, and never later than its five-second
     * deadline; the margin covers a broadcast delivered to a process that was starting.
     */
    val SCREENED_WINDOW: Duration = Duration.ofSeconds(15)
  }
}
