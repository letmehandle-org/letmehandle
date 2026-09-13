package org.letmehandle.app.calls

import java.io.File
import javax.xml.parsers.DocumentBuilderFactory
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.w3c.dom.Document
import org.w3c.dom.Element

/** Checks the merged manifest, dependencies included, requests no audio recording permission. */
class NoAudioPermissionTest {
  private val forbidden = setOf("android.permission.RECORD_AUDIO", "android.permission.CAPTURE_AUDIO_OUTPUT")

  private fun parse(file: File): Document =
      DocumentBuilderFactory.newInstance().apply { isNamespaceAware = true }.newDocumentBuilder().parse(file)

  private fun requestedPermissions(manifest: Document): Set<String> =
      listOf("uses-permission", "uses-permission-sdk-23")
          .flatMap { tag ->
            val nodes = manifest.getElementsByTagName(tag)
            (0 until nodes.length).map { (nodes.item(it) as Element).getAttributeNS(ANDROID, "name") }
          }
          .toSet()

  private fun merged(): Document {
    val path = checkNotNull(System.getProperty("mergedManifest")) { "mergedManifest is set by the build" }
    return parse(File(path))
  }

  @Test
  fun `the manifest read is the merged one`() {
    // Only the merger writes uses-sdk, so this proves the merged manifest is the one read.
    assertEquals(1, merged().getElementsByTagName("uses-sdk").length)
    assertTrue(requestedPermissions(merged()).contains("android.permission.READ_PHONE_STATE"))
  }

  @Test
  fun `the app as built asks for no permission to record audio`() {
    val held = requestedPermissions(merged()) intersect forbidden
    assertTrue("the merged manifest requests $held", held.isEmpty())
  }

  @Test
  fun `the check finds a recording permission wherever a manifest asks for it`() {
    val planted = File.createTempFile("manifest", ".xml")
    try {
      planted.writeText(
          """
          <manifest xmlns:android="$ANDROID">
            <uses-permission android:name="android.permission.RECORD_AUDIO" />
            <uses-permission-sdk-23 android:name="android.permission.CAPTURE_AUDIO_OUTPUT" />
          </manifest>
          """
              .trimIndent())
      assertEquals(forbidden, requestedPermissions(parse(planted)) intersect forbidden)
    } finally {
      planted.delete()
    }
  }

  private companion object {
    const val ANDROID = "http://schemas.android.com/apk/res/android"
  }
}
