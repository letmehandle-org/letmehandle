/**
 * The checks that run before a save, and the one rule that decides what may be skipped.
 *
 * These do not keep bad data out — the server does that — so each test is about the user being
 * told sooner, not about the data being safe.
 */
import {
  contactProblem,
  normaliseNumber,
  normaliseTopic,
  timeProblem,
  topicProblem,
  windowProblem,
} from '../preferences/validation';

describe('times and windows', () => {
  it.each(['09:00', '00:00', '23:59'])('accepts %s', value => {
    expect(timeProblem(value)).toBeNull();
  });

  it.each(['9:00', '24:00', '09:60', 'nine', ''])('refuses %s', value => {
    expect(timeProblem(value)).toBe('preferences.hours.invalidTime');
  });

  it('accepts a window that runs past midnight', () => {
    // Ten at night until seven is the ordinary quiet hours, not a mistake.
    expect(
      windowProblem({ start: '22:00', end: '07:00', zone: 'Europe/London' }),
    ).toBeNull();
  });

  it('refuses a window that covers nothing', () => {
    expect(
      windowProblem({ start: '09:00', end: '09:00', zone: 'Europe/London' }),
    ).toBe('preferences.hours.emptyWindow');
  });

  it('reports a bad start before looking any further', () => {
    expect(windowProblem({ start: '9', end: '25:00', zone: '' })).toBe(
      'preferences.hours.invalidTime',
    );
  });

  it('reports a bad end', () => {
    expect(
      windowProblem({ start: '09:00', end: '25:00', zone: 'Europe/London' }),
    ).toBe('preferences.hours.invalidTime');
  });

  it('refuses a window with no zone, which would be compared against the server clock', () => {
    expect(windowProblem({ start: '09:00', end: '17:00', zone: '  ' })).toBe(
      'preferences.hours.invalidZone',
    );
  });
});

describe('numbers', () => {
  it.each([
    ['+1 (202) 555-0143', '+12025550143'],
    ['0044 20 7946 0958', '+442079460958'],
    ['  +442079460958  ', '+442079460958'],
  ])('normalises %s', (raw, expected) => {
    expect(normaliseNumber(raw)).toBe(expected);
  });
});

describe('important contacts', () => {
  const existing = [
    {
      label: 'School',
      phone_number: '+12025550143',
      posture: 'reject' as const,
    },
  ];

  it('accepts one that is new and complete', () => {
    expect(
      contactProblem(
        { label: 'Dentist', phone_number: '+442079460958', posture: 'reject' },
        existing,
      ),
    ).toBeNull();
  });

  it('refuses one with no name, because the list would be numbers', () => {
    expect(
      contactProblem(
        { label: '  ', phone_number: '+442079460958', posture: 'reject' },
        existing,
      ),
    ).toBe('preferences.important_contacts.invalidLabel');
  });

  it('refuses a label longer than the backend stores', () => {
    expect(
      contactProblem(
        {
          label: 'a'.repeat(81),
          phone_number: '+442079460958',
          posture: 'reject',
        },
        existing,
      ),
    ).toBe('preferences.important_contacts.invalidLabel');
  });

  it('refuses a number with no country code', () => {
    expect(
      contactProblem(
        { label: 'Dentist', phone_number: '2079460958', posture: 'reject' },
        existing,
      ),
    ).toBe('preferences.important_contacts.invalidNumber');
  });

  it('refuses a number already on the list, however it was typed', () => {
    // The server would accept a second entry for the same number. It is the screen that knows
    // two rules for one caller is not something anybody meant.
    expect(
      contactProblem(
        {
          label: 'School office',
          phone_number: '+1 (202) 555-0143',
          posture: 'reject',
        },
        existing,
      ),
    ).toBe('preferences.important_contacts.duplicate');
  });
});

describe('topics', () => {
  it('folds spacing and case together, so one topic is not three', () => {
    expect(normaliseTopic('  School   Run ')).toBe('school run');
  });

  it('accepts a phrase', () => {
    expect(topicProblem('School Run', [])).toBeNull();
  });

  it('refuses an empty one', () => {
    expect(topicProblem('   ', [])).toBe(
      'preferences.personality.invalidTopic',
    );
  });

  it('refuses a sentence, which is an instruction in a list the agent reads', () => {
    expect(topicProblem('a'.repeat(61), [])).toBe(
      'preferences.personality.invalidTopic',
    );
  });

  it('refuses one already on the list under a different spelling', () => {
    expect(topicProblem('School Run', ['school run'])).toBe(
      'preferences.personality.duplicateTopic',
    );
  });
});
