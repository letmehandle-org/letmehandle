package org.letmehandle.app.calls.bridge

import com.facebook.react.bridge.Promise
import org.letmehandle.app.calls.events.StoreUnavailable
import org.letmehandle.app.calls.rules.CallRulesSnapshotCodec

/** Resolves with [block]'s result (null for Unit), or rejects with a code for each failure the app handles. */
internal fun Promise.settle(block: () -> Any?) {
  val result =
      try {
        block()
      } catch (invalid: CallRulesSnapshotCodec.InvalidSnapshot) {
        reject("invalid_snapshot", invalid.message, invalid)
        return
      } catch (unavailable: StoreUnavailable) {
        reject("store_unavailable", unavailable.message, unavailable)
        return
      }
  resolve(result.takeUnless { it == Unit })
}
