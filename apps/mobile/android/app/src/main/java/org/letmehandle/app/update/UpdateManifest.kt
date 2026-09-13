package org.letmehandle.app.update

import java.net.URI
import java.net.URISyntaxException
import java.util.Locale
import org.json.JSONException
import org.json.JSONObject

/**
 * The newest published APK, as the release workflow describes it in `letmehandle-android.json`.
 *
 * [sha256] is what makes the download trustworthy: the manifest and the APK are fetched separately,
 * and an APK whose digest is not this one is never handed to the installer.
 */
data class UpdateManifest(
    val versionCode: Int,
    val versionName: String,
    val apkUrl: String,
    val sha256: String,
)

/**
 * Reads the manifest strictly. A manifest with anything unexpected is refused whole, and a refused
 * manifest means no update — the app keeps running the version it has, and asks again later.
 */
object UpdateManifestCodec {
  class InvalidManifest(message: String, cause: Throwable? = null) :
      IllegalArgumentException(message, cause)

  private val sha256Hex = Regex("[0-9a-f]{64}")

  fun decode(text: String): UpdateManifest =
      try {
        read(JSONObject(text))
      } catch (error: JSONException) {
        throw InvalidManifest("the update manifest is not the expected document", error)
      }

  private fun read(document: JSONObject): UpdateManifest {
    // getInt would accept 1.9 as 1 and "7" as 7; a version is compared, so it must be exactly a number.
    val versionCode = document.get("versionCode")
    if (versionCode !is Int || versionCode < 1) {
      throw InvalidManifest("the update manifest's versionCode is not a positive whole number")
    }
    val versionName = document.getString("versionName").trim()
    if (versionName.isEmpty()) {
      throw InvalidManifest("the update manifest has no versionName")
    }
    val apkUrl = document.getString("apkUrl")
    if (!isHttpsUrl(apkUrl)) {
      throw InvalidManifest("the update manifest's apkUrl is not an https URL")
    }
    val sha256 = document.getString("sha256").lowercase(Locale.ROOT)
    if (!sha256Hex.matches(sha256)) {
      throw InvalidManifest("the update manifest's sha256 is not 64 hexadecimal digits")
    }
    return UpdateManifest(versionCode, versionName, apkUrl, sha256)
  }

  private fun isHttpsUrl(text: String): Boolean =
      try {
        val uri = URI(text)
        uri.scheme == "https" && !uri.host.isNullOrEmpty() && uri.userInfo == null
      } catch (error: URISyntaxException) {
        false
      }
}
