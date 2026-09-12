package org.letmehandle.app.calls.bridge

import com.facebook.react.BaseReactPackage
import com.facebook.react.bridge.NativeModule
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.module.model.ReactModuleInfo
import com.facebook.react.module.model.ReactModuleInfoProvider
import org.letmehandle.app.specs.NativeCallScreeningSpec

/** Registers the call screening module with React Native, as a TurboModule. */
class CallScreeningPackage : BaseReactPackage() {
  override fun getModule(name: String, reactContext: ReactApplicationContext): NativeModule? =
      if (name == NativeCallScreeningSpec.NAME) CallScreeningModule(reactContext) else null

  override fun getReactModuleInfoProvider(): ReactModuleInfoProvider = ReactModuleInfoProvider {
    mapOf(
        NativeCallScreeningSpec.NAME to
            ReactModuleInfo(
                name = NativeCallScreeningSpec.NAME,
                className = NativeCallScreeningSpec.NAME,
                canOverrideExistingModule = false,
                needsEagerInit = false,
                isCxxModule = false,
                isTurboModule = true,
            )
    )
  }
}
