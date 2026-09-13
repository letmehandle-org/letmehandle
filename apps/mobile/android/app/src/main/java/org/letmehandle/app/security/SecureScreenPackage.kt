package org.letmehandle.app.security

import com.facebook.react.BaseReactPackage
import com.facebook.react.bridge.NativeModule
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.module.model.ReactModuleInfo
import com.facebook.react.module.model.ReactModuleInfoProvider
import org.letmehandle.app.specs.NativeSecureScreenSpec

/** Registers the secure screen module with React Native, as a TurboModule. */
class SecureScreenPackage : BaseReactPackage() {
  override fun getModule(name: String, reactContext: ReactApplicationContext): NativeModule? =
      if (name == NativeSecureScreenSpec.NAME) SecureScreenModule(reactContext) else null

  override fun getReactModuleInfoProvider(): ReactModuleInfoProvider = ReactModuleInfoProvider {
    mapOf(
        NativeSecureScreenSpec.NAME to
            ReactModuleInfo(
                name = NativeSecureScreenSpec.NAME,
                className = NativeSecureScreenSpec.NAME,
                canOverrideExistingModule = false,
                needsEagerInit = false,
                isCxxModule = false,
                isTurboModule = true,
            )
    )
  }
}
