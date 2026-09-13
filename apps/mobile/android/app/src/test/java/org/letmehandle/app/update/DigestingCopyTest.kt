package org.letmehandle.app.update

import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class DigestingCopyTest {
  // The SHA-256 of "abc", from FIPS 180-2's examples.
  private val abcSha256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"

  @Test
  fun `what is copied is what is hashed`() {
    val output = ByteArrayOutputStream()
    val sha256 = DigestingCopy.copy(ByteArrayInputStream("abc".toByteArray()), output, maxBytes = 3)
    assertEquals(abcSha256, sha256)
    assertArrayEquals("abc".toByteArray(), output.toByteArray())
  }

  @Test
  fun `a download larger than allowed is abandoned`() {
    assertThrows(DigestingCopy.TooLarge::class.java) {
      DigestingCopy.copy(ByteArrayInputStream(ByteArray(200_000)), ByteArrayOutputStream(), maxBytes = 199_999)
    }
  }

  @Test
  fun `an APK is accepted only with the digest the manifest states`() {
    assertTrue(DigestingCopy.matches(abcSha256, abcSha256.uppercase()))
    assertFalse(DigestingCopy.matches(abcSha256, abcSha256.replaceFirst('b', 'c')))
    assertFalse(DigestingCopy.matches("", ""))
    assertFalse(DigestingCopy.matches(abcSha256, ""))
  }
}
