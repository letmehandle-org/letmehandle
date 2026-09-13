package org.letmehandle.app.security

import android.view.WindowManager
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.UiThreadUtil
import org.letmehandle.app.specs.NativeSecureScreenSpec

/** Sets or clears the window's secure flag on the UI thread (D-035). */
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
