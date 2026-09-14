package org.letmehandle.app.update

import java.io.File
import javax.xml.parsers.DocumentBuilderFactory
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.w3c.dom.Element

/** What the app as built declares for updating itself. */
class SelfUpdateManifestEntriesTest {
  private val manifest by lazy {
    val path = checkNotNull(System.getProperty("mergedManifest")) { "mergedManifest is set by the build" }
    DocumentBuilderFactory.newInstance().apply { isNamespaceAware = true }.newDocumentBuilder().parse(File(path))
  }

  private fun elements(tag: String): List<Element> =
      manifest.getElementsByTagName(tag).let { nodes -> (0 until nodes.length).map { nodes.item(it) as Element } }

  @Test
  fun `the app may ask to install its own updates`() {
    assertTrue(
        elements("uses-permission").any {
          it.getAttributeNS(ANDROID, "name") == "android.permission.REQUEST_INSTALL_PACKAGES"
        })
  }

  @Test
  fun `the install result receiver is not exported`() {
    val receiver = elements("receiver").single { it.getAttributeNS(ANDROID, "name").endsWith(".update.UpdateInstallResultReceiver") }
    assertEquals("false", receiver.getAttributeNS(ANDROID, "exported"))
  }

  private companion object {
    const val ANDROID = "http://schemas.android.com/apk/res/android"
  }
}
