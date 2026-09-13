package org.letmehandle.app

import com.facebook.react.ReactActivity
import com.facebook.react.ReactActivityDelegate
import com.facebook.react.defaults.DefaultNewArchitectureEntryPoint.fabricEnabled
import com.facebook.react.defaults.DefaultReactActivityDelegate

class MainActivity : ReactActivity() {

  /** The name of the main component registered from JavaScript. */
  override fun getMainComponentName(): String = "LetMeHandle"

  /** The React activity delegate, with the New Architecture enabled when [fabricEnabled] is. */
  override fun createReactActivityDelegate(): ReactActivityDelegate =
      DefaultReactActivityDelegate(this, mainComponentName, fabricEnabled)
}
