package org.letmehandle.app.calls.bridge

import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.WritableMap
import org.junit.Assert.assertEquals
import org.junit.Test
import org.letmehandle.app.calls.events.StoreUnavailable
import org.letmehandle.app.calls.rules.CallRulesSnapshotCodec

class PromiseSettlementTest {
  private class RecordingPromise : Promise {
    val outcomes = mutableListOf<String>()

    override fun resolve(value: Any?) {
      outcomes += "resolved $value"
    }

    override fun reject(code: String?, message: String?, throwable: Throwable?) {
      outcomes += "rejected $code"
    }

    override fun reject(code: String?, message: String?) = reject(code, message, null as Throwable?)

    override fun reject(code: String?, throwable: Throwable?) = reject(code, null as String?, throwable)

    override fun reject(throwable: Throwable) = reject(null, null as String?, throwable)

    override fun reject(throwable: Throwable, userInfo: WritableMap) = reject(null, null as String?, throwable)

    override fun reject(code: String?, userInfo: WritableMap) = reject(code, null as String?, null as Throwable?)

    override fun reject(code: String?, throwable: Throwable?, userInfo: WritableMap) = reject(code, null as String?, throwable)

    override fun reject(code: String?, message: String?, userInfo: WritableMap) = reject(code, message, null as Throwable?)

    override fun reject(code: String?, message: String?, throwable: Throwable?, userInfo: WritableMap?) =
        reject(code, message, throwable)

    @Deprecated("unused") override fun reject(message: String) = reject(null, message, null as Throwable?)
  }

  private fun outcomeOf(block: () -> Any?): List<String> = RecordingPromise().apply { settle(block) }.outcomes

  @Test
  fun `a completed call resolves with its result`() {
    assertEquals(listOf("resolved done"), outcomeOf { "done" })
  }

  @Test
  fun `a call with no result resolves with null`() {
    assertEquals(listOf("resolved null"), outcomeOf {})
  }

  @Test
  fun `a snapshot the handset cannot read is rejected as invalid`() {
    assertEquals(listOf("rejected invalid_snapshot"), outcomeOf { throw CallRulesSnapshotCodec.InvalidSnapshot("unreadable") })
  }

  @Test
  fun `storage that refuses a write rejects the promise instead of ending the app`() {
    assertEquals(listOf("rejected store_unavailable"), outcomeOf { throw StoreUnavailable() })
  }
}
