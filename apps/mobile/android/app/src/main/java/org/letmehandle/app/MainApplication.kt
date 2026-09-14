package org.letmehandle.app

import android.app.Application
import com.facebook.react.PackageList
import com.facebook.react.ReactApplication
import com.facebook.react.ReactHost
import com.facebook.react.ReactNativeApplicationEntryPoint.loadReactNative
import com.facebook.react.defaults.DefaultReactHost.getDefaultReactHost
import org.letmehandle.app.calls.bridge.CallScreeningPackage
import org.letmehandle.app.device.DeviceCountryPackage
import org.letmehandle.app.security.SecureScreenPackage
import org.letmehandle.app.update.ForegroundEntries
import org.letmehandle.app.update.SelfUpdater

class MainApplication : Application(), ReactApplication {

  override val reactHost: ReactHost by lazy {
    getDefaultReactHost(
      context = applicationContext,
      packageList =
        PackageList(this).packages.apply {
          // The app's own modules, which autolinking does not register.
          add(CallScreeningPackage())
          add(SecureScreenPackage())
          add(DeviceCountryPackage())
        },
    )
  }

  override fun onCreate() {
    super.onCreate()
    loadReactNative(this)
    val selfUpdater = SelfUpdater(this, BuildConfig.UPDATE_MANIFEST_URL, BuildConfig.VERSION_CODE)
    registerActivityLifecycleCallbacks(ForegroundEntries(selfUpdater::checkIfDue))
  }
}
