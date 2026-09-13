package org.letmehandle.app.device

import com.facebook.react.BaseReactPackage
import com.facebook.react.bridge.NativeModule
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.module.model.ReactModuleInfo
import com.facebook.react.module.model.ReactModuleInfoProvider
import org.letmehandle.app.specs.NativeDeviceCountrySpec

/** Registers the device country module with React Native, as a TurboModule. */
class DeviceCountryPackage : BaseReactPackage() {
  override fun getModule(name: String, reactContext: ReactApplicationContext): NativeModule? =
      if (name == NativeDeviceCountrySpec.NAME) DeviceCountryModule(reactContext) else null

  override fun getReactModuleInfoProvider(): ReactModuleInfoProvider = ReactModuleInfoProvider {
    mapOf(
        NativeDeviceCountrySpec.NAME to
            ReactModuleInfo(
                name = NativeDeviceCountrySpec.NAME,
                className = NativeDeviceCountrySpec.NAME,
                canOverrideExistingModule = false,
                needsEagerInit = false,
                isCxxModule = false,
                isTurboModule = true,
            )
    )
  }
}
