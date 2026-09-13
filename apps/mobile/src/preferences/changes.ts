/** Applies a partial change to preferences. */
import type { Preferences, PreferencesUpdate } from '@letmehandle/api-client';

/** The preferences once this change lands; an absent or null section is left as it was. */
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
    privacy: changes.privacy ?? current.privacy,
  };
}
