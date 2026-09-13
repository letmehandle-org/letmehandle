package org.letmehandle.app.calls

import java.io.File
import org.json.JSONArray
import org.json.JSONObject

/** The example documents shared with the TypeScript tests, from `src/calls/wire-examples.json`. */
object WireExamples {
  private val document: JSONObject by lazy {
    val path = checkNotNull(System.getProperty("wireExamples")) { "wireExamples is set by the build" }
    JSONObject(File(path).readText())
  }

  fun readableSnapshots(): List<JSONObject> = document.getJSONObject("snapshots").getJSONArray("readable").objects()

  fun refusedSnapshots(): List<JSONObject> = document.getJSONObject("snapshots").getJSONArray("refused").objects()

  fun events(): List<JSONObject> = document.getJSONArray("events").objects()

  private fun JSONArray.objects(): List<JSONObject> = (0 until length()).map(::getJSONObject)
}
