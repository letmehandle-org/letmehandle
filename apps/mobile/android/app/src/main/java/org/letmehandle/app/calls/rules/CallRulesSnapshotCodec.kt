package org.letmehandle.app.calls.rules

import java.time.Instant
import org.json.JSONObject
import org.letmehandle.app.calls.objects
import org.letmehandle.app.calls.readingJson
import org.letmehandle.app.calls.strings
import org.letmehandle.app.calls.wireValueOf

/** The snapshot's wire form shared with `src/calls/wire.ts`, refusing any document it cannot read whole. */
object CallRulesSnapshotCodec {
  // Version 2 drops quiet hours (D-030).
  const val VERSION = 2

  class InvalidSnapshot(message: String, cause: Throwable? = null) :
      IllegalArgumentException(message, cause)

  fun decode(text: String): CallRulesSnapshot =
      readingJson({ failure -> failure as? InvalidSnapshot ?: InvalidSnapshot("the snapshot is not a document this build reads", failure) }) {
        read(JSONObject(text))
      }

  private fun read(document: JSONObject): CallRulesSnapshot {
    val version = document.getInt("version")
    if (version != VERSION) {
      throw InvalidSnapshot("the snapshot was written in version $version")
    }
    val byCategory = document.getJSONObject("posture_by_category")
    return CallRulesSnapshot(
        syncedAt = Instant.parse(document.getString("synced_at")),
        defaultPosture = wireValueOf(document.getString("default_posture")),
        anonymousPosture = wireValueOf(document.getString("anonymous_posture")),
        postureByCategory =
            byCategory.keys().asSequence().associate { key ->
              wireValueOf<CallerCategory>(key) to wireValueOf<HandlingPosture>(byCategory.getString(key))
            },
        blockedCategories = document.getJSONArray("blocked_categories").strings().map { wireValueOf<CallerCategory>(it) }.toSet(),
        importantContacts =
            document.getJSONArray("important_contacts").objects().map { contact ->
              ImportantContact(
                  phoneNumber = contact.getString("phone_number"),
                  posture = wireValueOf(contact.getString("posture")),
              )
            },
    )
  }
}
