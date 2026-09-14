package org.letmehandle.app.update

import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class UpdatePolicyTest {
  private val hour = 60 * 60 * 1000L
  private val policy = UpdatePolicy(checkIntervalMillis = 4 * hour)

  private fun manifest(versionCode: Int) =
      UpdateManifest(versionCode, "v", "https://example.com/a.apk", "0".repeat(64))

  @Test
  fun `a build without a manifest address never updates itself`() {
    assertFalse(policy.isEnabled(""))
    assertFalse(policy.isEnabled("   "))
    assertTrue(policy.isEnabled("https://example.com/letmehandle-android.json"))
  }

  @Test
  fun `the first check is due, and the next only after the interval`() {
    val start = 1_000_000_000L
    assertTrue(policy.isCheckDue(lastCheckStartedAtMillis = null, nowMillis = start))
    assertFalse(policy.isCheckDue(start, start))
    assertFalse(policy.isCheckDue(start, start + 4 * hour - 1))
    assertTrue(policy.isCheckDue(start, start + 4 * hour))
  }

  @Test
  fun `a clock moved back does not postpone checks until it catches up`() {
    assertTrue(policy.isCheckDue(lastCheckStartedAtMillis = 10 * hour, nowMillis = 2 * hour))
  }

  @Test
  fun `only a strictly newer version is an update`() {
    assertTrue(policy.offersUpdate(manifest(2026091402), installedVersionCode = 2026091400))
    assertFalse(policy.offersUpdate(manifest(2026091400), installedVersionCode = 2026091400))
    assertFalse(policy.offersUpdate(manifest(2026091300), installedVersionCode = 2026091400))
  }

  @Test
  fun `the interval must be positive`() {
    assertThrows(IllegalArgumentException::class.java) { UpdatePolicy(checkIntervalMillis = 0) }
  }
}
