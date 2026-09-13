package org.letmehandle.app.device

import android.content.Context
import android.telephony.TelephonyManager
import com.facebook.react.bridge.ReactApplicationContext
import org.letmehandle.app.specs.NativeDeviceCountrySpec

/** The network's country, or the SIM's when there is no network, for the app's JavaScript. */
class DeviceCountryModule(private val context: ReactApplicationContext) :
    NativeDeviceCountrySpec(context) {

  override fun networkCountry(): String? {
    val telephony = context.getSystemService(Context.TELEPHONY_SERVICE) as? TelephonyManager ?: return null
    return telephony.networkCountryIso?.takeIf { it.isNotBlank() }
        ?: telephony.simCountryIso?.takeIf { it.isNotBlank() }
  }
}
