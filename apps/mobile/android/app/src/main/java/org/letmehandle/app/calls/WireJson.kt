package org.letmehandle.app.calls

import java.time.DateTimeException
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject

/** An enum value with the spelling the backend and the app's JavaScript use for it. */
interface WireValue {
  val wire: String
}

/** The [T] spelt [value] on the wire; throws [IllegalArgumentException] for a spelling this build does not know. */
inline fun <reified T> wireValueOf(value: String): T where T : Enum<T>, T : WireValue =
    enumValues<T>().firstOrNull { it.wire == value } ?: throw IllegalArgumentException("not a value this build knows")

/** Runs [read], turning every failure malformed JSON raises into the exception [refuse] builds. */
inline fun <T> readingJson(refuse: (Exception) -> Exception, read: () -> T): T =
    try {
      read()
    } catch (failure: JSONException) {
      throw refuse(failure)
    } catch (failure: IllegalArgumentException) {
      throw refuse(failure)
    } catch (failure: DateTimeException) {
      throw refuse(failure)
    }

/** The string at [key], or null when it is absent or JSON null. */
fun JSONObject.optStringOrNull(key: String): String? = if (has(key) && !isNull(key)) getString(key) else null

fun JSONArray.strings(): List<String> = (0 until length()).map(::getString)

fun JSONArray.objects(): List<JSONObject> = (0 until length()).map(::getJSONObject)
