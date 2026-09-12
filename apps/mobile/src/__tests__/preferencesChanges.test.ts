/**
 * Which sections a change touches, and which it must not.
 *
 * Both functions exist to stop the same defect: saving one section quietly changing another.
 */
import { applyChanges, withWholeCallRules } from '../preferences/changes';
import { DEFAULT_PREFERENCES } from './support/backend';

describe('showing a change before it is saved', () => {
  it('replaces only the sections the change mentions', () => {
    const shown = applyChanges(DEFAULT_PREFERENCES, {
      personality: { formality: 'warm', verbosity: 'brief', topics: ['bins'] },
    });

    expect(shown.personality.formality).toBe('warm');
    expect(shown.notifications).toEqual(DEFAULT_PREFERENCES.notifications);
    expect(shown.call_handling).toEqual(DEFAULT_PREFERENCES.call_handling);
  });

  it('treats a null section the way the backend does, as nothing said', () => {
    const shown = applyChanges(DEFAULT_PREFERENCES, { authority: null });

    expect(shown.authority).toEqual(DEFAULT_PREFERENCES.authority);
  });

  it('keeps the version, which only the server sets', () => {
    const shown = applyChanges(DEFAULT_PREFERENCES, { locale: 'fr' });

    expect(shown.locale).toBe('fr');
    expect(shown.version).toBe(DEFAULT_PREFERENCES.version);
  });

  it('replaces the contact list rather than adding to it', () => {
    // The backend stores what it is sent, so a half-list here would be a half-list there.
    const shown = applyChanges(DEFAULT_PREFERENCES, {
      important_contacts: [
        { label: 'School', phone_number: '+12025550143', posture: 'reject' },
      ],
    });

    expect(shown.important_contacts).toHaveLength(1);
  });
});

describe('keeping call handling and hours together', () => {
  it('fills in the hours when only the handling changed', () => {
    // The server builds one object from both and fills the half it was not sent from its
    // defaults, so sending handling alone would clear hours the user had set.
    const stored = {
      ...DEFAULT_PREFERENCES,
      hours: {
        working: { start: '09:00', end: '17:30', zone: 'Europe/London' },
        quiet: null,
      },
    };

    const request = withWholeCallRules(stored, {
      call_handling: {
        ...DEFAULT_PREFERENCES.call_handling,
        default_posture: 'reject',
      },
    });

    expect(request.hours).toEqual(stored.hours);
    expect(request.call_handling?.default_posture).toBe('reject');
  });

  it('fills in the handling when only the hours changed', () => {
    const request = withWholeCallRules(DEFAULT_PREFERENCES, {
      hours: {
        working: null,
        quiet: { start: '22:00', end: '07:00', zone: 'Europe/London' },
      },
    });

    expect(request.call_handling).toEqual(DEFAULT_PREFERENCES.call_handling);
  });

  it('leaves a change that touches neither exactly as it was', () => {
    const changes = { notifications: DEFAULT_PREFERENCES.notifications };

    expect(withWholeCallRules(DEFAULT_PREFERENCES, changes)).toEqual(changes);
  });
});
