package org.letmehandle.app.calls.rules

import java.time.Instant
import org.json.JSONObject
import org.letmehandle.app.calls.objects
import org.letmehandle.app.calls.readingJson
import org.letmehandle.app.calls.strings
import org.letmehandle.app.calls.wireValueOf

/**
 * The snapshot's wire form, shared with `src/calls/wire.ts`.
 *
 * Strict: a document with a value this does not recognise is refused whole rather than read in
 * part. A rule half-understood is a rule applied wrongly, and the screening service treats a
 * refused snapshot exactly as it treats none — the call rings.
 *
 * The example documents both sides are tested against live in `src/calls/wire-examples.json`.
 */
object CallRulesSnapshotCodec {
  // 2 dropped quiet hours (D-030). A version 1 snapshot is refused like any other unknown format,
  // so the call rings until the app, on its next open, writes the rules again.
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
