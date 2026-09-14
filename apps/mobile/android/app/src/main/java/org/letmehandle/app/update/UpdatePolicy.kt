package org.letmehandle.app.update

/**
 * When to ask for a newer version, and whether the answer is one.
 *
 * The app is opened and brought back many times a day; asking each time would spend the user's data
 * and the release host's rate limit on answers that almost never change. So a check happens at most
 * once per [checkIntervalMillis], counted from when the last one started, whether it succeeded or
 * not: a failure is tried again at the next check rather than in a loop.
 */
class UpdatePolicy(private val checkIntervalMillis: Long = DEFAULT_CHECK_INTERVAL_MILLIS) {
  init {
    require(checkIntervalMillis > 0) { "the check interval must be positive" }
  }

  fun isEnabled(manifestUrl: String): Boolean = manifestUrl.isNotBlank()

  /**
   * A last check in the future means the clock was moved back; waiting for the clock to catch up
   * could mean never checking again, so that counts as due.
   */
  fun isCheckDue(lastCheckStartedAtMillis: Long?, nowMillis: Long): Boolean =
      lastCheckStartedAtMillis == null ||
          lastCheckStartedAtMillis > nowMillis ||
          nowMillis - lastCheckStartedAtMillis >= checkIntervalMillis

  /** Only strictly newer: the installer refuses a downgrade, and the same version is nothing to do. */
  fun offersUpdate(manifest: UpdateManifest, installedVersionCode: Int): Boolean =
      manifest.versionCode > installedVersionCode

  companion object {
    const val DEFAULT_CHECK_INTERVAL_MILLIS: Long = 4 * 60 * 60 * 1000L
  }
}
