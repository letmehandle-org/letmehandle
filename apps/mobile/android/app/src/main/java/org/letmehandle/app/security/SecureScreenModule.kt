package org.letmehandle.app.security

import android.view.WindowManager
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.UiThreadUtil
import org.letmehandle.app.specs.NativeSecureScreenSpec

/**
 * The window's secure flag, for screens that show what callers said.
 *
 * With it set, the system refuses screenshots and screen recording and shows a blank card in the
 * recent apps list. Set on the UI thread, because a window's flags belong to it.
 */
class SecureScreenModule(private val context: ReactApplicationContext) :
    NativeSecureScreenSpec(context) {

  override fun setSecure(secure: Boolean) {
    UiThreadUtil.runOnUiThread {
      val window = context.currentActivity?.window ?: return@runOnUiThread
      if (secure) {
        window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
      } else {
        window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
      }
    }
  }
}
