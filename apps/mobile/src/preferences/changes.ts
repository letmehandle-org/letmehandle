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

/**
 * Send call handling and hours together, or send neither.
 *
 * They are two screens here and one object on the server, and the server fills the half it was
 * not sent from its *defaults* rather than from what is stored. So a patch carrying only the
 * quiet hours resets how unknown callers are treated — silently, and to something the user
 * never chose. Sending both halves every time is what stops that.
 */
export function withWholeCallRules(
  current: Preferences,
  changes: PreferencesUpdate,
): PreferencesUpdate {
  const touchesEither =
    changes.call_handling !== undefined || changes.hours !== undefined;
  if (!touchesEither) {
    return changes;
  }

  return {
    ...changes,
    call_handling: changes.call_handling ?? current.call_handling,
    hours: changes.hours ?? current.hours,
  };
}
