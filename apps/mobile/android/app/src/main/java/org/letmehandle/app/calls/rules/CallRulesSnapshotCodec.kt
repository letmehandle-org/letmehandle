package org.letmehandle.app.calls.rules

import java.time.Instant
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject

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
      try {
        read(JSONObject(text))
      } catch (error: JSONException) {
        throw InvalidSnapshot("the snapshot is not the expected document", error)
      } catch (error: InvalidSnapshot) {
        throw error
      } catch (error: IllegalArgumentException) {
        throw InvalidSnapshot("the snapshot holds a value the handset does not recognise", error)
      } catch (error: java.time.DateTimeException) {
        throw InvalidSnapshot("the snapshot holds a time or timezone that cannot be read", error)
      }

  private fun read(document: JSONObject): CallRulesSnapshot {
    val version = document.getInt("version")
    if (version != VERSION) {
      throw InvalidSnapshot("the snapshot was written in version $version")
    }
    val byCategory = document.getJSONObject("posture_by_category")
    return CallRulesSnapshot(
        syncedAt = Instant.parse(document.getString("synced_at")),
        defaultPosture = HandlingPosture.fromWire(document.getString("default_posture")),
        anonymousPosture = HandlingPosture.fromWire(document.getString("anonymous_posture")),
        postureByCategory =
            byCategory.keys().asSequence().associate { key ->
              CallerCategory.fromWire(key) to HandlingPosture.fromWire(byCategory.getString(key))
            },
        blockedCategories =
            document.getJSONArray("blocked_categories").strings().map(CallerCategory::fromWire).toSet(),
        importantContacts =
            document.getJSONArray("important_contacts").objects().map { contact ->
              ImportantContact(
                  phoneNumber = contact.getString("phone_number"),
                  posture = HandlingPosture.fromWire(contact.getString("posture")),
              )
            },
    )
  }

  private fun JSONArray.strings(): List<String> = (0 until length()).map(::getString)

  private fun JSONArray.objects(): List<JSONObject> = (0 until length()).map(::getJSONObject)
}
