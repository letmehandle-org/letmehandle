package org.letmehandle.app.calls.events

import java.time.DateTimeException
import java.time.Instant
import org.json.JSONException
import org.json.JSONObject
import org.letmehandle.app.calls.rules.ScreeningDecision

/**
 * What a handset can observe about its own call. Mirrors the backend's `ReportedCallKind`, which
 * is the same vocabulary every transport reports in, less what a handset cannot see.
 */
enum class CallEventKind(val wire: String) {
  INCOMING("incoming"),
  ANSWERED("answered"),
  ENDED("ended"),
}

/** How a call ended. Mirrors `CallEnding`. */
enum class CallEnding(val wire: String) {
  SCREENED_OUT("screened_out"),
  MISSED("missed"),
  COMPLETED("completed"),
}

/**
 * One thing that happened to one call, waiting to be reported.
 *
 * Its wire form is exactly the backend's `CallReportPayload`, so the app forwards what it is
 * given without translating it. The example documents in `src/calls/wire-examples.json` are read
 * by this codec's tests and by the TypeScript side's, which is what holds the two in step.
 */
data class CallEventRecord(
    val eventId: String,
    val callId: String,
    val kind: CallEventKind,
    val occurredAt: Instant,
    val callerNumber: String? = null,
    val screening: ScreeningDecision? = null,
    val ending: CallEnding? = null,
) {
  init {
    require(screening == null || kind == CallEventKind.INCOMING) {
      "a screening decision is reported on the incoming event only"
    }
    require((ending != null) == (kind == CallEventKind.ENDED)) {
      "an ended call says how it ended, and nothing else does"
    }
  }

  fun toJson(): JSONObject =
      JSONObject().apply {
        put("event_id", eventId)
        put("call_id", callId)
        put("kind", kind.wire)
        put("occurred_at", occurredAt.toString())
        callerNumber?.let { put("caller_number", it) }
        screening?.let { put("screening", it.wire) }
        ending?.let { put("ending", it.wire) }
      }

  /** A stored entry that is not a call event this build can read. Its message quotes nothing. */
  class UnreadableRecord(cause: Throwable?) :
      IllegalArgumentException("a stored call event could not be read", cause)

  companion object {
    /** Reads one stored event, or throws [UnreadableRecord] and nothing else. */
    fun fromJson(json: JSONObject): CallEventRecord =
        try {
          read(json)
        } catch (error: JSONException) {
          throw UnreadableRecord(error)
        } catch (error: IllegalArgumentException) {
          throw UnreadableRecord(error)
        } catch (error: DateTimeException) {
          throw UnreadableRecord(error)
        }

    private fun read(json: JSONObject): CallEventRecord =
        CallEventRecord(
            eventId = json.getString("event_id"),
            callId = json.getString("call_id"),
            kind = wireValue(CallEventKind.entries, json.getString("kind")) { it.wire },
            occurredAt = Instant.parse(json.getString("occurred_at")),
            callerNumber = json.optStringOrNull("caller_number"),
            screening =
                json.optStringOrNull("screening")?.let { value ->
                  wireValue(ScreeningDecision.entries, value) { it.wire }
                },
            ending =
                json.optStringOrNull("ending")?.let { value -> wireValue(CallEnding.entries, value) { it.wire } },
        )

    private fun <T> wireValue(values: List<T>, value: String, wire: (T) -> String): T =
        requireNotNull(values.firstOrNull { wire(it) == value }) { "not a value this build knows" }

    private fun JSONObject.optStringOrNull(key: String): String? =
        if (has(key) && !isNull(key)) getString(key) else null
  }
}
