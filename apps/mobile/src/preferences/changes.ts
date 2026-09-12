/**
 * Turning a partial change into the whole of what the user should see.
 *
 * Two jobs, both of which exist because a screen saves one section and the user looks at all of
 * them.
 */
import type { Preferences, PreferencesUpdate } from '@letmehandle/api-client';

/**
 * What the preferences will look like once this change lands.
 *
 * Used to show the change before the server has confirmed it. An absent or null section means
 * the change said nothing about it, which is the same thing the backend does with it: leave it
 * exactly as it was.
 */
export function applyChanges(
  current: Preferences,
  changes: PreferencesUpdate,
): Preferences {
  return {
    version: current.version,
    locale: changes.locale ?? current.locale,
    call_handling: changes.call_handling ?? current.call_handling,
    important_contacts:
      changes.important_contacts ?? current.important_contacts,
    hours: changes.hours ?? current.hours,
    authority: changes.authority ?? current.authority,
    notifications: changes.notifications ?? current.notifications,
    personality: changes.personality ?? current.personality,
  };
}
