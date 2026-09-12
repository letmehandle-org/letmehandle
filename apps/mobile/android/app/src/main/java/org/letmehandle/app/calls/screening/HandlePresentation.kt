package org.letmehandle.app.calls.screening

import android.telecom.TelecomManager
import org.letmehandle.app.calls.rules.CallerNumber
import org.letmehandle.app.calls.rules.ScreenedCaller

/**
 * A call's handle presentation, as the caller it describes.
 *
 * Only [TelecomManager.PRESENTATION_RESTRICTED] is a caller withholding their number. Unknown and
 * unavailable mean the network did not deliver one, and payphone is a line that has none to give;
 * refusing those under the anonymous posture would refuse people who hid nothing. The
 * `CallScreeningService.onScreenCall` documentation says none of the four is passed to a screening
 * service at all, so this is the answer for a platform that departs from it, not the common path.
 * Any presentation this build does not know is treated the same way: a call is never refused on a
 * value nobody can explain.
 */
object HandlePresentation {
  fun callerOf(presentation: Int, handleNumber: String?): ScreenedCaller =
      when (presentation) {
        TelecomManager.PRESENTATION_ALLOWED -> ScreenedCaller.Presented(CallerNumber.parse(handleNumber))
        TelecomManager.PRESENTATION_RESTRICTED -> ScreenedCaller.Withheld
        else -> ScreenedCaller.NotDelivered
      }
}
