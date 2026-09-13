package org.letmehandle.app.calls.screening

import android.telecom.TelecomManager
import org.letmehandle.app.calls.rules.CallerNumber
import org.letmehandle.app.calls.rules.ScreenedCaller

/** Maps a handle presentation to a caller; only a restricted presentation is withheld. */
object HandlePresentation {
  fun callerOf(presentation: Int, handleNumber: String?): ScreenedCaller =
      when (presentation) {
        TelecomManager.PRESENTATION_ALLOWED -> ScreenedCaller.Presented(CallerNumber.parse(handleNumber))
        TelecomManager.PRESENTATION_RESTRICTED -> ScreenedCaller.Withheld
        else -> ScreenedCaller.NotDelivered
      }
}
