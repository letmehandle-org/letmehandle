package org.letmehandle.app.calls.events

import java.time.Instant
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.letmehandle.app.calls.WireExamples
import org.letmehandle.app.calls.rules.ScreeningDecision

class CallEventLedgerTest {
  private class MemoryStore : TextStore {
    val values = mutableMapOf<String, String>()

    override fun read(key: String): String? = values[key]

    override fun write(key: String, value: String?) {
      if (value == null) values.remove(key) else values[key] = value
    }
  }

  private val store = MemoryStore()
  private var counter = 0
  private var changes = 0
  private val overflows = mutableListOf<Int>()
  private val now = Instant.parse("2026-09-13T11:00:00Z")

  private fun ledger(capacity: Int = CallEventLedger.DEFAULT_CAPACITY) =
      CallEventLedger(
          store = store,
          tracker = CallStateTracker { "id-${++counter}" },
          onChanged = { changes++ },
          onOverflow = { overflows += it },
          capacity = capacity,
      )

  @Test
  fun `events wait until the backend has them, across a restart`() {
    ledger().screened("+12025550145", ScreeningDecision.SILENCE, now)
    // A new ledger over the same store is a process that was started for the next broadcast.
    ledger().phoneState(PhoneState.RINGING, now.plusSeconds(1))
    ledger().phoneState(PhoneState.IDLE, now.plusSeconds(20))

    val pending = ledger().pending()
    assertEquals(listOf(CallEventKind.INCOMING, CallEventKind.ENDED), pending.map { it.kind })
    assertEquals(pending[0].callId, pending[1].callId)
    assertEquals(2, changes)
  }

  @Test
  fun `acknowledged events are forgotten and the rest kept`() {
    val ledger = ledger()
    ledger.screened(null, ScreeningDecision.REJECT, now)
    val (incoming, ended) = ledger.pending()

    ledger.acknowledge(listOf(incoming.eventId))

    assertEquals(listOf(ended), ledger.pending())
  }

  @Test
  fun `a state that produces nothing does not announce a change`() {
    ledger().phoneState(PhoneState.IDLE, now)
    assertEquals(0, changes)
  }

  @Test
  fun `the oldest events go first when the handset holds too many, and that is said`() {
    val ledger = ledger(capacity = 3)
    ledger.screened(null, ScreeningDecision.REJECT, now)
    ledger.screened(null, ScreeningDecision.REJECT, now.plusSeconds(1))

    val kept = ledger.pending()
    assertEquals(3, kept.size)
    assertEquals(now, kept.first().occurredAt)
    assertEquals(CallEventKind.ENDED, kept.first().kind)
    assertEquals(listOf(1), overflows)
  }

  @Test
  fun `signing out forgets the account's calls`() {
    val ledger = ledger()
    ledger.screened(null, ScreeningDecision.ALLOW, now)
    ledger.clear()
    assertTrue(ledger.pending().isEmpty())
    assertTrue(store.values.isEmpty())
  }

  @Test
  fun `every example event reads and writes back as the same document`() {
    val examples = WireExamples.events()
    assertTrue(examples.isNotEmpty())
    examples.forEach { example ->
      val record = CallEventRecord.fromJson(example)
      val written = record.toJson()
      assertEquals(example.keys().asSequence().toSet(), written.keys().asSequence().toSet())
      example.keys().forEach { key -> assertEquals(key, example.getString(key), written.getString(key)) }
    }
  }

  @Test(expected = IllegalArgumentException::class)
  fun `a decision on a later event is refused`() {
    CallEventRecord("e", "c", CallEventKind.ANSWERED, now, screening = ScreeningDecision.ALLOW)
  }

  @Test(expected = IllegalArgumentException::class)
  fun `an ended event must say how`() {
    CallEventRecord("e", "c", CallEventKind.ENDED, now)
  }
}
