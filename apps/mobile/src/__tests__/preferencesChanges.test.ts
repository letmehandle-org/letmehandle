/**
 * Which sections a change touches, and which it must not.
 *
 * Both functions exist to stop the same defect: saving one section quietly changing another.
 */
import { applyChanges } from '../preferences/changes';
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

describe('keeping call handling and hours together', () => {});
