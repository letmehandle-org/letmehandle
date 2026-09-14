package org.letmehandle.app.update

import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.security.MessageDigest

/** Copies a download while computing its SHA-256, within a byte limit. */
object DigestingCopy {
  class TooLarge(maxBytes: Long) : IOException("the download is larger than $maxBytes bytes")

  /** Returns the lowercase hexadecimal SHA-256 of everything copied. */
  fun copy(from: InputStream, to: OutputStream, maxBytes: Long): String {
    val digest = MessageDigest.getInstance("SHA-256")
    val buffer = ByteArray(BUFFER_BYTES)
    var copied = 0L
    while (true) {
      val read = from.read(buffer)
      if (read < 0) break
      copied += read
      if (copied > maxBytes) throw TooLarge(maxBytes)
      digest.update(buffer, 0, read)
      to.write(buffer, 0, read)
    }
    return digest.digest().toHex()
  }

  /** Compared case-insensitively; the manifest codec already lowercases, this does not rely on it. */
  fun matches(actualHex: String, expectedHex: String): Boolean =
      actualHex.length == SHA256_HEX_LENGTH && actualHex.equals(expectedHex, ignoreCase = true)

  private fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it) }

  private const val BUFFER_BYTES = 64 * 1024
  private const val SHA256_HEX_LENGTH = 64
}
