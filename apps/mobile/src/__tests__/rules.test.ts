/**
 * The translation between the design's rules and the API's preferences.
 */
import { arcsFor } from '../components/dialGeometry';
import {
  coveredMinutes,
  deviceZone,
  firstWindow,
  hoursFigure,
  onTheRing,
  TIMES,
  withEnd,
} from '../preferences/hours';
import {
  MAX_FACTS,
  MAX_FACT_LENGTH,
  factProblem,
  followsTwoLanes,
  grantedCount,
  hearsEveryCall,
  personalityWith,
  twoLanes,
  withEveryCall,
} from '../preferences/rules';
import { DEFAULT_PREFERENCES } from './support/backend';

const handling = DEFAULT_PREFERENCES.call_handling;

describe('the two lanes', () => {
  it('keeps the threshold it was given', () => {
    expect(
      twoLanes({ ...handling, escalate_at_or_above: 40 }).escalate_at_or_above,
    ).toBe(40);
  });

  it('recognises itself', () => {
    expect(followsTwoLanes(twoLanes(handling))).toBe(true);
  });

  it.each([
    ['another default', { default_posture: 'pass_through' }],
    ['anonymous calls rung through', { anonymous_posture: 'pass_through' }],
    [
      'a second category',
      {
        posture_by_category: {
          known_contact: 'pass_through',
          delivery: 'handle_with_agent',
        },
      },
    ],
    [
      'contacts sent to the assistant',
      { posture_by_category: { known_contact: 'handle_with_agent' } },
    ],
    ['nothing blocked', { blocked_categories: [] }],
    ['something else blocked', { blocked_categories: ['sales'] }],
    [
      'no categories at all',
      { posture_by_category: undefined, blocked_categories: undefined },
    ],
  ])('does not mistake %s for the lanes', (_name, change) => {
    expect(
      followsTwoLanes({ ...twoLanes(handling), ...change } as typeof handling),
    ).toBe(false);
  });
});

describe('every call', () => {
  const off = {
    ...DEFAULT_PREFERENCES.notifications,
    on_handled_call: false,
    on_blocked_call: false,
  };

  it('is on only when both halves are', () => {
    expect(hearsEveryCall(off)).toBe(false);
    expect(hearsEveryCall({ ...off, on_handled_call: true })).toBe(false);
    expect(hearsEveryCall(withEveryCall(off, true))).toBe(true);
    expect(hearsEveryCall(withEveryCall(withEveryCall(off, true), false))).toBe(
      false,
    );
  });
});

describe('hours', () => {
  const window = (start: string, end: string) => ({
    start,
    end,
    zone: 'Europe/London',
  });

  it('offers every half hour of the day, in order', () => {
    expect(TIMES).toHaveLength(48);
    expect(TIMES[0]).toBe('00:00');
    expect(TIMES[19]).toBe('09:30');
    expect(TIMES[47]).toBe('23:30');
  });

  it('measures a window inside the day and one that runs past midnight', () => {
    expect(coveredMinutes(window('09:30', '18:30'))).toBe(540);
    expect(coveredMinutes(window('22:00', '07:00'))).toBe(540);
    expect(hoursFigure(window('09:30', '18:30'))).toEqual({
      count: 9,
      hours: '9',
    });
    expect(hoursFigure(window('09:00', '17:30'))).toEqual({
      count: 8.5,
      hours: '8.5',
    });
  });

  it('places the window on the ring where it falls in the day', () => {
    expect(onTheRing(window('06:00', '18:00'))).toEqual({
      start: 0.25,
      fraction: 0.5,
    });
  });

  it('never makes a window that starts where it ends', () => {
    expect(withEnd(window('09:00', '17:00'), 'end', '09:00')).toBeNull();
    expect(withEnd(window('09:00', '17:00'), 'start', '08:00')).toEqual(
      window('08:00', '17:00'),
    );
  });

  it('starts somebody who sets hours on a working day, in the phone’s zone', () => {
    expect(firstWindow('Asia/Kolkata')).toEqual({
      start: '09:00',
      end: '17:00',
      zone: 'Asia/Kolkata',
    });
    expect(deviceZone()).toEqual(expect.any(String));
  });

  it('falls back to UTC on a phone that names no zone', () => {
    const real = Intl.DateTimeFormat;
    Intl.DateTimeFormat = (() => ({
      resolvedOptions: () => ({ timeZone: '' }),
    })) as unknown as typeof Intl.DateTimeFormat;
    try {
      expect(deviceZone()).toBe('UTC');
    } finally {
      Intl.DateTimeFormat = real;
    }
  });
});

describe('capabilities', () => {
  it('counts what is granted, treating none as zero', () => {
    const none = {
      ...DEFAULT_PREFERENCES,
      authority: { ...DEFAULT_PREFERENCES.authority, capabilities: undefined },
    };
    expect(grantedCount(none, 7)).toEqual({ granted: 0, total: 7 });
    const two = {
      ...DEFAULT_PREFERENCES,
      authority: {
        ...DEFAULT_PREFERENCES.authority,
        capabilities: ['take_a_message', 'confirm_appointments'],
      },
    };
    expect(grantedCount(two as typeof DEFAULT_PREFERENCES, 7)).toEqual({
      granted: 2,
      total: 7,
    });
  });
});

describe('personality', () => {
  it('carries the parts it was not asked to change, filling missing lists', () => {
    const preferences = {
      ...DEFAULT_PREFERENCES,
      personality: {
        formality: 'warm',
        verbosity: 'brief',
        topics: undefined,
        disclosable_facts: undefined,
      },
    } as unknown as typeof DEFAULT_PREFERENCES;
    expect(personalityWith(preferences, { verbosity: 'detailed' })).toEqual({
      formality: 'warm',
      verbosity: 'detailed',
      topics: [],
      disclosable_facts: [],
    });
  });
});

describe('facts', () => {
  it('refuses empty, overlong, duplicate and one too many', () => {
    expect(factProblem('   ', [])).toBe('say.invalid');
    expect(factProblem('x'.repeat(MAX_FACT_LENGTH + 1), [])).toBe(
      'say.invalid',
    );
    expect(factProblem(' Free after six ', ['free after SIX'])).toBe(
      'say.duplicate',
    );
    expect(
      factProblem(
        'One more',
        Array.from({ length: MAX_FACTS }, (_, i) => `fact ${i}`),
      ),
    ).toBe('say.full');
    expect(factProblem('x'.repeat(MAX_FACT_LENGTH), [])).toBeNull();
  });
});

describe('the ring', () => {
  it('lays segments end to end', () => {
    expect(
      arcsFor(
        [
          { fraction: 0.25, colour: 'a' },
          { fraction: 0.5, colour: 'b' },
        ],
        100,
      ),
    ).toEqual([
      { colour: 'a', length: 25, offset: 0 },
      { colour: 'b', length: 50, offset: 25 },
    ]);
  });

  it('draws nothing for empty or negative parts and clips at the whole', () => {
    expect(
      arcsFor(
        [
          { fraction: 0, colour: 'a' },
          { fraction: -1, colour: 'b' },
          { fraction: 0.75, colour: 'c' },
          { fraction: 0.5, colour: 'd' },
          { fraction: 0.1, colour: 'e' },
        ],
        200,
      ),
    ).toEqual([
      { colour: 'c', length: 150, offset: 0 },
      { colour: 'd', length: 50, offset: 150 },
    ]);
  });
});
