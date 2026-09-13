package org.letmehandle.app.calls.events

import java.time.Instant
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.letmehandle.app.calls.FailureSummary
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
  private val unreadable = mutableListOf<Throwable>()
  private val now = Instant.parse("2026-09-13T11:00:00Z")

  /** A ledger over the one store, recording for a signed-in account unless [signedIn] is false. */
  private fun ledger(capacity: Int = CallEventLedger.DEFAULT_CAPACITY, signedIn: Boolean = true) =
      CallEventLedger(
              store = store,
              tracker = CallStateTracker { "id-${++counter}" },
              onChanged = { changes++ },
              onOverflow = { overflows += it },
              onUnreadable = { unreadable += it },
              capacity = capacity,
          )
          .also { if (signedIn) it.startRecording() }

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
  fun `nothing is recorded before any account has signed in`() {
    val ledger = ledger(signedIn = false)
    ledger.screened("+12025550145", ScreeningDecision.REJECT, now)
    ledger.phoneState(PhoneState.RINGING, now.plusSeconds(1))
    ledger.phoneState(PhoneState.IDLE, now.plusSeconds(20))

    assertTrue(ledger.pending().isEmpty())
    assertTrue(store.values.isEmpty())
    assertEquals(0, changes)
  }

  @Test
  fun `calls while nobody is signed in are never reported to whoever signs in next`() {
    // A shared phone: one account signs out, somebody's calls arrive, another account signs in.
    val ledger = ledger()
    ledger.clear()
    ledger.screened("+12025550145", ScreeningDecision.SILENCE, now)
    ledger.phoneState(PhoneState.RINGING, now.plusSeconds(1))
    // A process started for the next broadcast, while still nobody is signed in.
    ledger(signedIn = false).phoneState(PhoneState.IDLE, now.plusSeconds(20))

    ledger.startRecording()

    assertTrue(ledger.pending().isEmpty())
    ledger.screened(null, ScreeningDecision.REJECT, now.plusSeconds(60))
    assertEquals(listOf(now.plusSeconds(60), now.plusSeconds(60)), ledger.pending().map { it.occurredAt })
  }

  @Test
  fun `a call already ringing when an account signs in is not half reported`() {
    val ledger = ledger(signedIn = false)
    ledger.phoneState(PhoneState.RINGING, now)
    ledger.startRecording()
    ledger.phoneState(PhoneState.IDLE, now.plusSeconds(5))

    assertTrue(ledger.pending().isEmpty())
  }

  /** Three stored events, `event-1` to `event-3`, with [corrupt] stored at [position] among them. */
  private fun storeWithCorruptEntryAt(position: Int, corrupt: Any = JSONObject().put("kind", "incoming")) {
    val entries: MutableList<Any> =
        (1..3)
            .map { index ->
              CallEventRecord("event-$index", "call-$index", CallEventKind.INCOMING, now.plusSeconds(index.toLong()))
                  .toJson()
            }
            .toMutableList()
    entries.add(position, corrupt)
    store.values[CallEventLedger.PENDING] = JSONArray(entries).toString()
  }

  @Test
  fun `a corrupt first entry is dropped and the events behind it are still delivered`() {
    storeWithCorruptEntryAt(0)
    assertEquals(listOf("event-1", "event-2", "event-3"), ledger().pending().map { it.eventId })
    assertEquals(1, unreadable.size)
  }

  @Test
  fun `a corrupt entry in the middle is dropped and the rest delivered`() {
    storeWithCorruptEntryAt(2, corrupt = JSONObject(mapOf("event_id" to "e", "call_id" to "c", "kind" to "vanished")))
    assertEquals(listOf("event-1", "event-2", "event-3"), ledger().pending().map { it.eventId })
    assertEquals(1, unreadable.size)
  }

  @Test
  fun `a corrupt last entry is dropped and the rest delivered`() {
    storeWithCorruptEntryAt(3, corrupt = "not an event")
    assertEquals(listOf("event-1", "event-2", "event-3"), ledger().pending().map { it.eventId })
    assertEquals(1, unreadable.size)
  }

  @Test
  fun `a dropped entry is written out of the ledger, so it fails one read and not every one`() {
    storeWithCorruptEntryAt(1)
    val ledger = ledger()
    ledger.pending()
    ledger.pending()
    assertEquals(1, unreadable.size)
    assertEquals(3, JSONArray(store.values.getValue(CallEventLedger.PENDING)).length())
  }

  @Test
  fun `an event recorded behind a corrupt entry is still delivered and acknowledged`() {
    storeWithCorruptEntryAt(3)
    val ledger = ledger()
    ledger.screened(null, ScreeningDecision.ALLOW, now.plusSeconds(10))
    val pending = ledger.pending()
    assertEquals(4, pending.size)

    ledger.acknowledge(pending.map { it.eventId })

    assertTrue(ledger.pending().isEmpty())
    assertEquals(1, unreadable.size)
  }

  @Test
  fun `storage that is not a list at all is dropped whole and recording carries on`() {
    store.values[CallEventLedger.PENDING] = "[{\"caller_number\":\"+12025550145\""
    val ledger = ledger()
    assertTrue(ledger.pending().isEmpty())
    assertEquals(1, unreadable.size)

    ledger.screened(null, ScreeningDecision.REJECT, now)
    assertEquals(2, ledger.pending().size)
    assertEquals(1, unreadable.size)
  }

  @Test
  fun `a corrupt tracked call is forgotten and screening and phone states still record`() {
    store.values[CallEventLedger.TRACKED] = "{\"call_id\":7,\"ringing\":\"yes\"}"
    val ledger = ledger()

    ledger.screened("+12025550145", ScreeningDecision.SILENCE, now)
    ledger.phoneState(PhoneState.RINGING, now.plusSeconds(1))

    assertEquals(listOf(CallEventKind.INCOMING), ledger.pending().map { it.kind })
    assertEquals(1, unreadable.size)
  }

  @Test
  fun `what is said about a dropped entry names its failure, never the number it held`() {
    storeWithCorruptEntryAt(0, corrupt = JSONObject(mapOf("caller_number" to "+12025550145")))
    ledger().pending()
    assertFalse(FailureSummary.of(unreadable.single()).contains("2025550145"))
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
