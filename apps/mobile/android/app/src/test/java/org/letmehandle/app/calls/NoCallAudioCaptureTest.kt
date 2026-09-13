package org.letmehandle.app.calls

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

/** Checks the app's own sources name no audio capture API or permission (D-013). */
class NoCallAudioCaptureTest {
  private val main = File("src/main")

  private val forbidden =
      listOf(
          "AudioRecord",
          "MediaRecorder",
          "VOICE_CALL",
          "VOICE_DOWNLINK",
          "VOICE_UPLINK",
          "VOICE_COMMUNICATION",
          "AudioPlaybackCaptureConfiguration",
          "RECORD_AUDIO",
          "CAPTURE_AUDIO_OUTPUT",
      )

  private fun sources(): List<File> =
      main.walkTopDown().filter { it.isFile && it.extension in setOf("kt", "java", "xml") }.toList()

  @Test
  fun `the app's sources are where this test is looking`() {
    // Guards against the whole check passing because the path stopped matching anything.
    assertTrue(sources().any { it.name == "AndroidManifest.xml" })
    assertTrue(sources().any { it.name == "RulesScreeningService.kt" })
  }

  @Test
  fun `nothing in the app captures call audio or asks to`() {
    val offences =
        sources().flatMap { file ->
          file.readLines().mapIndexedNotNull { index, line ->
            forbidden
                .firstOrNull { Regex("\\b$it\\b").containsMatchIn(line) }
                ?.let { "${file.relativeTo(main)}:${index + 1} names $it" }
          }
        }
    assertTrue(offences.joinToString("\n"), offences.isEmpty())
  }

  @Test
  fun `the check finds what it is looking for`() {
    val planted = "val recorder = AudioRecord(MediaRecorder.AudioSource.VOICE_CALL, 8000, 16, 2, 1024)"
    assertTrue(forbidden.any { Regex("\\b$it\\b").containsMatchIn(planted) })
  }
}
