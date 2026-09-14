package org.letmehandle.app.update

import android.content.Context
import android.content.pm.PackageInstaller
import android.os.Build
import android.util.Log
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.IOException
import java.io.OutputStream
import java.net.URL
import java.util.concurrent.Executor
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import javax.net.ssl.HttpsURLConnection

/** Keeps an APK installed from a GitHub release on the newest release (D-043). */
class SelfUpdater(
    context: Context,
    private val manifestUrl: String,
    private val installedVersionCode: Int,
    private val policy: UpdatePolicy = UpdatePolicy(),
    private val executor: Executor = Executors.newSingleThreadExecutor(),
) {
  private val context = context.applicationContext
  private val checkInProgress = AtomicBoolean(false)

  /** Cheap to call on every return to the foreground; the policy decides whether anything is fetched. */
  fun checkIfDue() {
    if (!policy.isEnabled(manifestUrl)) return
    if (!checkInProgress.compareAndSet(false, true)) return
    executor.execute {
      try {
        checkNow()
      } catch (error: Exception) {
        Log.w(TAG, "update check failed: ${error.javaClass.name}: ${error.message}")
      } finally {
        checkInProgress.set(false)
      }
    }
  }

  private fun checkNow() {
    // Read here rather than on the caller's thread: the first read of preferences touches storage.
    val preferences = context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
    val now = System.currentTimeMillis()
    val lastCheckStartedAt =
        if (preferences.contains(LAST_CHECK_STARTED_AT)) preferences.getLong(LAST_CHECK_STARTED_AT, 0) else null
    if (!policy.isCheckDue(lastCheckStartedAt, now)) return
    // Recorded before the check, so a check that fails or kills the process still counts.
    preferences.edit().putLong(LAST_CHECK_STARTED_AT, now).commit()

    val downloads = File(context.cacheDir, DOWNLOAD_DIRECTORY)
    // A download left by a process that died mid-way is never resumed or installed.
    downloads.deleteRecursively()

    val manifest = UpdateManifestCodec.decode(String(fetch(manifestUrl, MAX_MANIFEST_BYTES), Charsets.UTF_8))
    if (!policy.offersUpdate(manifest, installedVersionCode)) return

    Log.i(TAG, "version ${manifest.versionName} (${manifest.versionCode}) is available; downloading")
    downloads.mkdirs()
    val apk = File(downloads, "update.apk")
    try {
      val actualSha256 = apk.outputStream().use { output -> download(manifest.apkUrl, output, MAX_APK_BYTES) }
      if (!DigestingCopy.matches(actualSha256, manifest.sha256)) {
        Log.w(TAG, "the downloaded APK's SHA-256 is not the manifest's; not installing it")
        return
      }
      install(apk)
    } finally {
      apk.delete()
    }
  }

  private fun fetch(url: String, maxBytes: Long): ByteArray =
      ByteArrayOutputStream().also { output -> download(url, output, maxBytes) }.toByteArray()

  /** Returns the SHA-256 of what was written. */
  private fun download(url: String, output: OutputStream, maxBytes: Long): String {
    val connection = URL(url).openConnection() as? HttpsURLConnection ?: throw IOException("only https is fetched")
    try {
      connection.connectTimeout = CONNECT_TIMEOUT_MILLIS
      connection.readTimeout = READ_TIMEOUT_MILLIS
      // Follows the release host's redirect; the final address must still be https.
      connection.instanceFollowRedirects = true
      val status = connection.responseCode
      if (connection.url.protocol != "https") throw IOException("redirected away from https")
      if (status != HttpsURLConnection.HTTP_OK) throw IOException("HTTP $status")
      return connection.inputStream.use { input -> DigestingCopy.copy(input, output, maxBytes) }
    } finally {
      connection.disconnect()
    }
  }

  private fun install(apk: File) {
    val installer = context.packageManager.packageInstaller
    val params =
        PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
          setAppPackageName(context.packageName)
          setSize(apk.length())
          // Installs without a prompt where the platform allows it.
          if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
          }
        }
    val sessionId = installer.createSession(params)
    try {
      installer.openSession(sessionId).use { session ->
        apk.inputStream().use { input ->
          session.openWrite("update.apk", 0, apk.length()).use { output ->
            input.copyTo(output)
            session.fsync(output)
          }
        }
        session.commit(UpdateInstallResultReceiver.statusReceiver(context, sessionId))
      }
    } catch (error: Exception) {
      installer.abandonSession(sessionId)
      throw error
    }
  }

  private companion object {
    const val TAG = "SelfUpdater"
    const val PREFERENCES = "org.letmehandle.app.update"
    const val LAST_CHECK_STARTED_AT = "last_check_started_at"
    const val DOWNLOAD_DIRECTORY = "update"
    const val MAX_MANIFEST_BYTES = 64L * 1024
    const val MAX_APK_BYTES = 512L * 1024 * 1024
    const val CONNECT_TIMEOUT_MILLIS = 15_000
    const val READ_TIMEOUT_MILLIS = 60_000
  }
}
