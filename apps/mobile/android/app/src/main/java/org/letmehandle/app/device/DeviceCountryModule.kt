package org.letmehandle.app.device

import android.content.Context
import android.telephony.TelephonyManager
import com.facebook.react.bridge.ReactApplicationContext
import org.letmehandle.app.specs.NativeDeviceCountrySpec

/**
 * The network's country, and the SIM's when there is no network, for the number screen.
 *
 * Neither needs a permission. The network comes first because roaming is when they differ, and
 * the network is where the phone is.
 */
class DeviceCountryModule(private val context: ReactApplicationContext) :
    NativeDeviceCountrySpec(context) {

  override fun networkCountry(): String? {
    val telephony = context.getSystemService(Context.TELEPHONY_SERVICE) as? TelephonyManager ?: return null
    return telephony.networkCountryIso?.takeIf { it.isNotBlank() }
        ?: telephony.simCountryIso?.takeIf { it.isNotBlank() }
  }
}
