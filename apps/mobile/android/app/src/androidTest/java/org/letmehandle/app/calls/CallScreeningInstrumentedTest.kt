package org.letmehandle.app.calls

import android.app.role.RoleManager
import android.content.ComponentName
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.telecom.CallScreeningService
import android.telephony.TelephonyManager
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import java.time.Instant
import java.util.concurrent.Executors
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.letmehandle.app.calls.events.CallEventKind
import org.letmehandle.app.calls.events.PhoneStateReceiver
import org.letmehandle.app.calls.rules.CallRulesSnapshotCodec
import org.letmehandle.app.calls.rules.CallerNumber
import org.letmehandle.app.calls.rules.ScreenedCaller
import org.letmehandle.app.calls.rules.Screening
import org.letmehandle.app.calls.rules.ScreeningDecision
import org.letmehandle.app.calls.rules.ScreeningReason
import org.letmehandle.app.calls.rules.ScreeningRules
import org.letmehandle.app.calls.screening.DeadlineScreener
import org.letmehandle.app.calls.screening.RulesScreeningService

/**
 * The screening path on a real Android runtime: the platform's own response objects, the
 * manifest the telecom service binds through, shared preferences, and the phone-state receiver.
 *
 * What this cannot do is place a SIM call. The platform binds a screening service only for a
 * call arriving through the telephony stack; that half is exercised on a handset and recorded in
 * the phase verification report.
 */
@RunWith(AndroidJUnit4::class)
class CallScreeningInstrumentedTest {
  private val context = InstrumentationRegistry.getInstrumentation().targetContext
  private val graph = CallScreeningGraph.get(context)
  private val caller = "+12025550145"

  private val rejectEverybody =
      """
      {"version":1,"synced_at":"%s","default_posture":"reject","anonymous_posture":"reject",
       "posture_by_category":{},"blocked_categories":[],"quiet_hours":null,"important_contacts":[]}
      """

  @Before
  fun requireScreeningSupport() {
    assumeTrue(Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q)
    graph.forgetAccount()
  }

  @After
  fun forget() {
    graph.forgetAccount()
  }

  @Test
  fun each_decision_becomes_the_platform_response_that_means_it() {
    val allow = RulesScreeningService.responseFor(ScreeningDecision.ALLOW)
    assertFalse(allow.disallowCall)
    assertFalse(allow.rejectCall)
    assertFalse(allow.silenceCall)

    val silence = RulesScreeningService.responseFor(ScreeningDecision.SILENCE)
    assertFalse(silence.disallowCall)
    assertTrue(silence.silenceCall)

    val reject = RulesScreeningService.responseFor(ScreeningDecision.REJECT)
    assertTrue(reject.disallowCall)
    assertTrue(reject.rejectCall)
    assertFalse(reject.silenceCall)
    // Left visible: the platform ignores this for an app that is not the carrier's or system's.
    assertFalse(reject.skipCallLog)
  }

  @Test
  fun the_telecom_service_can_bind_the_screening_service_and_nobody_else_can() {
    val services =
        context.packageManager.queryIntentServices(
            Intent(CallScreeningService.SERVICE_INTERFACE).setPackage(context.packageName),
            PackageManager.MATCH_ALL,
        )
    val declared = services.single().serviceInfo
    assertEquals(RulesScreeningService::class.java.name, declared.name)
    assertEquals("android.permission.BIND_SCREENING_SERVICE", declared.permission)
    assertTrue(declared.isEnabled)
  }

  @Test
  fun the_role_the_service_depends_on_exists_and_is_not_assumed() {
    val roles = context.getSystemService(RoleManager::class.java)
    assertTrue(roles.isRoleAvailable(RoleManager.ROLE_CALL_SCREENING))
    assertNotNull(roles.createRequestRoleIntent(RoleManager.ROLE_CALL_SCREENING))
  }

  @Test
  fun a_snapshot_written_by_the_app_is_what_the_service_reads() {
    graph.writeSnapshot(rejectEverybody.format(Instant.now()))
    val snapshot = graph.readSnapshot()
    assertNotNull(snapshot)

    graph.forgetAccount()
    assertNull(graph.readSnapshot())
  }

  @Test
  fun a_snapshot_the_handset_cannot_read_leaves_calls_ringing_rather_than_older_rules() {
    graph.writeSnapshot(rejectEverybody.format(Instant.now()))
    val newerFormat = rejectEverybody.format(Instant.now()).replace("\"version\":1", "\"version\":2")

    try {
      graph.writeSnapshot(newerFormat)
      throw AssertionError("a version this build does not know must be refused")
    } catch (expected: CallRulesSnapshotCodec.InvalidSnapshot) {
      assertNull(graph.readSnapshot())
    }
  }

  @Test
  fun a_screening_on_this_runtime_answers_well_inside_the_platform_deadline() {
    graph.writeSnapshot(rejectEverybody.format(Instant.now()))
    val responses = LinkedBlockingQueue<Screening>()
    val screened = ScreenedCaller(CallerNumber.parse(caller), withheld = false)
    val started = System.nanoTime()

    DeadlineScreener(Executors.newSingleThreadExecutor(), Executors.newSingleThreadScheduledExecutor())
        .screen(
            { ScreeningRules.evaluate(graph.readSnapshot(), screened, Instant.now()) },
            { failure -> throw AssertionError("screening failed", failure) },
            responses::add,
        )

    val screening = responses.poll(5, TimeUnit.SECONDS)
    val elapsed = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started)
    assertEquals(Screening(ScreeningDecision.REJECT, ScreeningReason.DEFAULT_POSTURE), screening)
    assertTrue("answered after ${elapsed}ms", elapsed < DeadlineScreener.DEFAULT_BUDGET_MILLIS)
  }

  @Test
  fun without_a_snapshot_the_service_lets_the_call_ring() {
    val screened = ScreenedCaller(CallerNumber.parse(caller), withheld = false)
    assertEquals(
        Screening(ScreeningDecision.ALLOW, ScreeningReason.NO_RULES),
        ScreeningRules.evaluate(graph.readSnapshot(), screened, Instant.now()),
    )
  }

  @Test
  fun the_phone_state_broadcast_becomes_the_shared_event_vocabulary() {
    val receiver = PhoneStateReceiver()
    graph.ledger.screened(caller, ScreeningDecision.SILENCE, Instant.now())

    for (state in
        listOf(
            TelephonyManager.EXTRA_STATE_RINGING,
            TelephonyManager.EXTRA_STATE_OFFHOOK,
            TelephonyManager.EXTRA_STATE_IDLE,
        )) {
      receiver.onReceive(
          context,
          Intent(TelephonyManager.ACTION_PHONE_STATE_CHANGED)
              .setComponent(ComponentName(context, PhoneStateReceiver::class.java))
              .putExtra(TelephonyManager.EXTRA_STATE, state),
      )
    }

    val pending = graph.ledger.pending()
    assertEquals(
        listOf(CallEventKind.INCOMING, CallEventKind.ANSWERED, CallEventKind.ENDED),
        pending.map { it.kind },
    )
    assertEquals(1, pending.map { it.callId }.toSet().size)

    graph.ledger.acknowledge(pending.map { it.eventId })
    assertTrue(graph.ledger.pending().isEmpty())
  }
}
