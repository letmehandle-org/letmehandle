package org.letmehandle.app.calls.events

import java.time.Instant
import org.json.JSONArray
import org.json.JSONObject
import org.letmehandle.app.calls.WireValue
import org.letmehandle.app.calls.optStringOrNull
import org.letmehandle.app.calls.readingJson
import org.letmehandle.app.calls.wireValueOf
import org.letmehandle.app.calls.rules.ScreeningDecision

/** What a handset observes about its own call; mirrors the backend's `ReportedCallKind`. */
enum class CallEventKind(override val wire: String) : WireValue {
  INCOMING("incoming"),
  ANSWERED("answered"),
  ENDED("ended"),
}

/** How a call ended. Mirrors `CallEnding`. */
enum class CallEnding(override val wire: String) : WireValue {
  SCREENED_OUT("screened_out"),
  MISSED("missed"),
  COMPLETED("completed"),
}

/** One event of one call awaiting report, in the wire form of the backend's `CallReportPayload`. */
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
    /** The events as a JSON array of call reports, the form both stored and handed to the app. */
    fun listToJson(events: List<CallEventRecord>): String = JSONArray(events.map { it.toJson() }).toString()

    /** Reads one stored event, or throws [UnreadableRecord] and nothing else. */
    fun fromJson(json: JSONObject): CallEventRecord =
        readingJson(::UnreadableRecord) {
          CallEventRecord(
              eventId = json.getString("event_id"),
              callId = json.getString("call_id"),
              kind = wireValueOf(json.getString("kind")),
              occurredAt = Instant.parse(json.getString("occurred_at")),
              callerNumber = json.optStringOrNull("caller_number"),
              screening = json.optStringOrNull("screening")?.let { wireValueOf<ScreeningDecision>(it) },
              ending = json.optStringOrNull("ending")?.let { wireValueOf<CallEnding>(it) },
          )
        }
  }
}

/** A stored record this build cannot read; its message quotes nothing it held. */
class UnreadableRecord(cause: Throwable?) : IllegalArgumentException("a stored call record could not be read", cause)
