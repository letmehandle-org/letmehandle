package org.letmehandle.app.calls.events

import java.time.Instant
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

  companion object {
    fun fromJson(json: JSONObject): CallEventRecord =
        CallEventRecord(
            eventId = json.getString("event_id"),
            callId = json.getString("call_id"),
            kind = CallEventKind.entries.first { it.wire == json.getString("kind") },
            occurredAt = Instant.parse(json.getString("occurred_at")),
            callerNumber = json.optStringOrNull("caller_number"),
            screening =
                json.optStringOrNull("screening")?.let { value ->
                  ScreeningDecision.entries.first { it.wire == value }
                },
            ending =
                json.optStringOrNull("ending")?.let { value ->
                  CallEnding.entries.first { it.wire == value }
                },
        )

    private fun JSONObject.optStringOrNull(key: String): String? =
        if (has(key) && !isNull(key)) getString(key) else null
  }
}
