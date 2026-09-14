package org.letmehandle.app.update

/** When to ask for a newer version, and whether the answer is one. */
class UpdatePolicy(private val checkIntervalMillis: Long = DEFAULT_CHECK_INTERVAL_MILLIS) {
  init {
    require(checkIntervalMillis > 0) { "the check interval must be positive" }
  }

  fun isEnabled(manifestUrl: String): Boolean = manifestUrl.isNotBlank()

  /** A check is due when none ran, the last one is in the future, or the interval has passed. */
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
