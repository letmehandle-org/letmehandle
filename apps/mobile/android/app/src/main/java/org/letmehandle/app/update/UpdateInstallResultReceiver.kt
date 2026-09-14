package org.letmehandle.app.update

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentSender
import android.content.pm.PackageInstaller
import android.os.Build
import android.util.Log

/**
 * Hears how an update install ended.
 *
 * The one status that needs action is pending user action: the platform will not install silently
 * (the first update of a browser-installed APK, Android before 12, or "install unknown apps" not yet
 * allowed), so its confirmation screen is shown. Not exported: only the installer session this app
 * committed can reach it, through the pending intent below.
 */
class UpdateInstallResultReceiver : BroadcastReceiver() {
  override fun onReceive(context: Context, intent: Intent) {
    when (val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)) {
      PackageInstaller.STATUS_PENDING_USER_ACTION -> askTheUser(context, intent)
      PackageInstaller.STATUS_SUCCESS -> Log.i(TAG, "update installed")
      else ->
          Log.w(TAG, "update not installed: status $status, ${intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE)}")
    }
  }

  private fun askTheUser(context: Context, intent: Intent) {
    val confirmation =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
          intent.getParcelableExtra(Intent.EXTRA_INTENT, Intent::class.java)
        } else {
          @Suppress("DEPRECATION") intent.getParcelableExtra(Intent.EXTRA_INTENT)
        }
    if (confirmation == null) {
      Log.w(TAG, "the installer asked for confirmation without saying how")
      return
    }
    try {
      context.startActivity(confirmation.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    } catch (error: RuntimeException) {
      // Android can refuse to open it while the app is in the background; the next check tries again.
      Log.w(TAG, "could not ask to confirm the update: ${error.javaClass.name}")
    }
  }

  companion object {
    private const val TAG = "SelfUpdater"

    fun statusReceiver(context: Context, sessionId: Int): IntentSender {
      val intent = Intent(context, UpdateInstallResultReceiver::class.java).setPackage(context.packageName)
      // Mutable because the installer adds the status to it; explicit, so nobody else can receive it.
      val mutability = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) PendingIntent.FLAG_MUTABLE else 0
      return PendingIntent.getBroadcast(context, sessionId, intent, PendingIntent.FLAG_UPDATE_CURRENT or mutability)
          .intentSender
    }
  }
}
