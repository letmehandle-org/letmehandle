package org.letmehandle.app.update

import android.app.Activity
import android.app.Application
import android.os.Bundle

/**
 * Calls [onEnter] when the app comes to the foreground: at launch, and on every return after all of
 * its screens were hidden. Counted by started activities, so moving between the app's own screens
 * is not a return.
 */
class ForegroundEntries(private val onEnter: () -> Unit) : Application.ActivityLifecycleCallbacks {
  private var startedActivities = 0

  override fun onActivityStarted(activity: Activity) {
    startedActivities += 1
    if (startedActivities == 1) onEnter()
  }

  override fun onActivityStopped(activity: Activity) {
    startedActivities = (startedActivities - 1).coerceAtLeast(0)
  }

  override fun onActivityCreated(activity: Activity, savedInstanceState: Bundle?) {}

  override fun onActivityResumed(activity: Activity) {}

  override fun onActivityPaused(activity: Activity) {}

  override fun onActivitySaveInstanceState(activity: Activity, outState: Bundle) {}

  override fun onActivityDestroyed(activity: Activity) {}
}
