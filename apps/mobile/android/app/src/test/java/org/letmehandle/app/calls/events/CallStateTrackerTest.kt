package org.letmehandle.app.calls.events

import java.time.Instant
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.letmehandle.app.calls.rules.ScreeningDecision

class CallStateTrackerTest {
  private var counter = 0
  private val tracker = CallStateTracker { "id-${++counter}" }
  private val start = Instant.parse("2026-09-13T11:00:00Z")
  private val caller = "+12025550145"

  private fun at(seconds: Long): Instant = start.plusSeconds(seconds)

  /** Runs a sequence and returns every event it produced, with the call left being followed. */
  private fun run(vararg steps: (TrackedCall?) -> Transition): Pair<TrackedCall?, List<CallEventRecord>> {
    var current: TrackedCall? = null
    val events = mutableListOf<CallEventRecord>()
    for (step in steps) {
      val transition = step(current)
      current = transition.call
      events += transition.events
    }
    return current to events
  }

  private fun screened(decision: ScreeningDecision, second: Long = 0) = { call: TrackedCall? ->
    tracker.screened(call, caller, decision, at(second))
  }

  private fun state(state: PhoneState, second: Long) = { call: TrackedCall? ->
    tracker.phoneState(call, state, at(second))
  }

  @Test
  fun `a screened call that is answered becomes incoming, answered and ended, as one call`() {
    val (left, events) =
        run(
            screened(ScreeningDecision.ALLOW),
            state(PhoneState.RINGING, 1),
            state(PhoneState.OFFHOOK, 6),
            state(PhoneState.IDLE, 90),
        )

    assertEquals(listOf(CallEventKind.INCOMING, CallEventKind.ANSWERED, CallEventKind.ENDED), events.map { it.kind })
    assertEquals(1, events.map { it.callId }.toSet().size)
    assertEquals(3, events.map { it.eventId }.toSet().size)
    assertEquals(ScreeningDecision.ALLOW, events[0].screening)
    assertEquals(caller, events[0].callerNumber)
    assertEquals(CallEnding.COMPLETED, events[2].ending)
    assertEquals(at(90), events[2].occurredAt)
    assertNull(left)
  }

  @Test
  fun `a silenced call nobody picks up is missed`() {
    val (_, events) =
        run(screened(ScreeningDecision.SILENCE), state(PhoneState.RINGING, 1), state(PhoneState.IDLE, 30))

    assertEquals(listOf(CallEventKind.INCOMING, CallEventKind.ENDED), events.map { it.kind })
    assertEquals(ScreeningDecision.SILENCE, events[0].screening)
    assertEquals(CallEnding.MISSED, events[1].ending)
  }

  @Test
  fun `a rejected call ends at once, because it never rings`() {
    val (left, events) = run(screened(ScreeningDecision.REJECT))

    assertEquals(listOf(CallEventKind.INCOMING, CallEventKind.ENDED), events.map { it.kind })
    assertEquals(CallEnding.SCREENED_OUT, events[1].ending)
    assertEquals(events[0].callId, events[1].callId)
    assertNull(left)
  }

  @Test
  fun `a call that rings without being screened is still reported, without a number or decision`() {
    // A caller in the user's contacts, or one withholding their number: the platform does not
    // pass either to a screening service.
    val (_, events) = run(state(PhoneState.RINGING, 0), state(PhoneState.OFFHOOK, 3), state(PhoneState.IDLE, 60))

    assertEquals(listOf(CallEventKind.INCOMING, CallEventKind.ANSWERED, CallEventKind.ENDED), events.map { it.kind })
    assertNull(events[0].screening)
    assertNull(events[0].callerNumber)
  }

  @Test
  fun `ringing long after a screening is a different call`() {
    val (_, events) =
        run(
            screened(ScreeningDecision.ALLOW),
            state(PhoneState.RINGING, CallStateTracker.SCREENED_WINDOW.seconds + 1),
        )

    assertEquals(2, events.map { it.callId }.toSet().size)
    assertNull(events[1].screening)
  }

  @Test
  fun `placing a call is not reported`() {
    val (left, events) = run(state(PhoneState.OFFHOOK, 0), state(PhoneState.IDLE, 60))
    assertTrue(events.isEmpty())
    assertNull(left)
  }

  @Test
  fun `a repeated broadcast changes nothing`() {
    val (_, events) =
        run(
            state(PhoneState.RINGING, 0),
            state(PhoneState.RINGING, 1),
            state(PhoneState.OFFHOOK, 2),
            state(PhoneState.OFFHOOK, 3),
            state(PhoneState.IDLE, 4),
            state(PhoneState.IDLE, 5),
        )
    assertEquals(listOf(CallEventKind.INCOMING, CallEventKind.ANSWERED, CallEventKind.ENDED), events.map { it.kind })
  }

  @Test
  fun `a call screened while another is in progress is reported and does not disturb it`() {
    val (left, events) =
        run(
            state(PhoneState.RINGING, 0),
            state(PhoneState.OFFHOOK, 2),
            screened(ScreeningDecision.SILENCE, 10),
            state(PhoneState.IDLE, 60),
        )

    assertEquals(
        listOf(CallEventKind.INCOMING, CallEventKind.ANSWERED, CallEventKind.INCOMING, CallEventKind.ENDED),
        events.map { it.kind },
    )
    assertEquals(events[0].callId, events[3].callId)
    assertEquals(CallEnding.COMPLETED, events[3].ending)
    assertNull(left)
  }

  @Test
  fun `a screened call still waiting to ring survives an idle broadcast within its window`() {
    val (left, _) = run(screened(ScreeningDecision.ALLOW), state(PhoneState.IDLE, 1))
    assertEquals(at(0), left?.screenedAt)

    val (expired, _) =
        run(screened(ScreeningDecision.ALLOW), state(PhoneState.IDLE, CallStateTracker.SCREENED_WINDOW.seconds + 1))
    assertNull(expired)
  }

  @Test
  fun `the call being followed survives being written down`() {
    val call = TrackedCall("call", screenedAt = start, ringing = true, answered = false)
    assertEquals(call, TrackedCall.fromJson(call.toJson().toString()))
    val unscreened = call.copy(screenedAt = null, answered = true)
    assertEquals(unscreened, TrackedCall.fromJson(unscreened.toJson().toString()))
  }
}
