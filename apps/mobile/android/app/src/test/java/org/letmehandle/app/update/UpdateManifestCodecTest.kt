package org.letmehandle.app.update

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class UpdateManifestCodecTest {
  private val digest = "ab".repeat(32)

  private fun document(
      versionCode: String = "2026091402",
      versionName: String = "\"2026.9.14-2\"",
      apkUrl: String = "\"https://example.com/releases/download/v2026.9.14-2/letmehandle-android.apk\"",
      sha256: String = "\"$digest\"",
  ) = """{"versionCode":$versionCode,"versionName":$versionName,"apkUrl":$apkUrl,"sha256":$sha256}"""

  private fun refused(text: String) {
    assertThrows(UpdateManifestCodec.InvalidManifest::class.java) { UpdateManifestCodec.decode(text) }
  }

  @Test
  fun `the manifest the release workflow writes is read`() {
    assertEquals(
        UpdateManifest(
            versionCode = 2026091402,
            versionName = "2026.9.14-2",
            apkUrl = "https://example.com/releases/download/v2026.9.14-2/letmehandle-android.apk",
            sha256 = digest,
        ),
        UpdateManifestCodec.decode(document()),
    )
  }

  @Test
  fun `a digest in capitals is the same digest`() {
    assertEquals(digest, UpdateManifestCodec.decode(document(sha256 = "\"${digest.uppercase()}\"")).sha256)
  }

  @Test
  fun `an APK anywhere but an https address is refused`() {
    refused(document(apkUrl = "\"http://example.com/letmehandle-android.apk\""))
    refused(document(apkUrl = "\"file:///sdcard/letmehandle-android.apk\""))
    refused(document(apkUrl = "\"https:///no-host.apk\""))
    refused(document(apkUrl = "\"https://user:secret@example.com/a.apk\""))
    refused(document(apkUrl = "\"not a url\""))
  }

  @Test
  fun `a version that is not a positive whole number is refused`() {
    refused(document(versionCode = "0"))
    refused(document(versionCode = "-3"))
    refused(document(versionCode = "2.5"))
    refused(document(versionCode = "\"2026091402\""))
    refused(document(versionCode = "99999999999"))
  }

  @Test
  fun `a digest that is not a SHA-256 is refused`() {
    refused(document(sha256 = "\"${digest.dropLast(1)}\""))
    refused(document(sha256 = "\"${"zz".repeat(32)}\""))
  }

  @Test
  fun `a missing field or a document that is not one is refused`() {
    refused(document(versionName = "\"  \""))
    refused("""{"versionCode":2,"versionName":"2","apkUrl":"https://example.com/a.apk"}""")
    refused("<html>not found</html>")
    refused("")
  }
}
