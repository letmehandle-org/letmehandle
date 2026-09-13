package org.letmehandle.app.calls.rules

import java.time.Instant
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import org.letmehandle.app.calls.WireExamples

class CallRulesSnapshotCodecTest {
  @Test
  fun `every document the app can write is read`() {
    val examples = WireExamples.readableSnapshots()
    assertTrue(examples.isNotEmpty())
    examples.forEach { CallRulesSnapshotCodec.decode(it.toString()) }
  }

  @Test
  fun `a document is read into the rules it states`() {
    val snapshot = CallRulesSnapshotCodec.decode(WireExamples.readableSnapshots().first().toString())

    assertEquals(Instant.parse("2026-09-13T11:00:00Z"), snapshot.syncedAt)
    assertEquals(HandlingPosture.HANDLE_WITH_AGENT, snapshot.defaultPosture)
    assertEquals(HandlingPosture.REJECT, snapshot.anonymousPosture)
    assertEquals(mapOf(CallerCategory.UNKNOWN to HandlingPosture.PASS_THROUGH), snapshot.postureByCategory)
    assertEquals(setOf(CallerCategory.SPAM), snapshot.blockedCategories)
    assertEquals(listOf(ImportantContact("+12025550143", HandlingPosture.PASS_THROUGH)), snapshot.importantContacts)
  }

  @Test
  fun `every document the handset must refuse is refused whole`() {
    val examples = WireExamples.refusedSnapshots()
    assertTrue(examples.isNotEmpty())
    examples.forEach { example ->
      try {
        CallRulesSnapshotCodec.decode(example.toString())
        fail("expected the handset to refuse $example")
      } catch (expected: CallRulesSnapshotCodec.InvalidSnapshot) {
        assertTrue(expected.message!!.isNotBlank())
      }
    }
  }

  @Test(expected = CallRulesSnapshotCodec.InvalidSnapshot::class)
  fun `text that is not a document is refused`() {
    CallRulesSnapshotCodec.decode("rules")
  }

  @Test(expected = CallRulesSnapshotCodec.InvalidSnapshot::class)
  fun `a sync time that cannot be read is refused`() {
    // A copy: the examples are shared by every test in the run.
    val example = JSONObject(WireExamples.readableSnapshots().first().toString())
    example.put("synced_at", "yesterday")
    CallRulesSnapshotCodec.decode(example.toString())
  }
}
