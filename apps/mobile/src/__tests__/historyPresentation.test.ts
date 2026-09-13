/**
 * The pure choices behind how a call is drawn, and the screenshot guard's counting.
 */
import { en } from '../i18n/locales/en';
import {
  byDay,
  dayAndMonth,
  durationParts,
  iconOf,
  offsetFrom,
  queryFor,
  timeOfDay,
  toneOf,
} from '../history/presentation';
import { secureWindow } from '../security/secureScreen';

const NOW = new Date(2026, 8, 13, 12, 0);

describe('how a call is coloured', () => {
  it.each([
    [
      { outcome: 'resolved_by_agent', status: 'ended', human_joined: false },
      'assistant',
    ],
    [
      { outcome: 'passed_through', status: 'ended', human_joined: false },
      'assistant',
    ],
    [
      { outcome: 'handed_to_user', status: 'ended', human_joined: true },
      'needsYou',
    ],
    [
      {
        outcome: 'unanswered_escalation',
        status: 'ended',
        human_joined: false,
      },
      'needsYou',
    ],
    [{ outcome: null, status: 'in_progress', human_joined: false }, 'needsYou'],
    [
      { outcome: 'rejected_by_rule', status: 'ended', human_joined: false },
      'quiet',
    ],
    [{ outcome: 'failed', status: 'ended', human_joined: false }, 'quiet'],
    [{ outcome: null, status: 'ended', human_joined: false }, 'quiet'],
  ] as const)('%j is %s', (call, tone) => {
    expect(toneOf(call)).toBe(tone);
  });

  it('draws a refused call as refused whatever kind of caller it was', () => {
    const bank = {
      category: 'financial' as const,
      display_name: null,
      number_withheld: false,
    };
    expect(iconOf(bank, 'resolved_by_agent')).toBe('card');
    expect(iconOf(bank, 'rejected_by_rule')).toBe('ban');
  });
});

describe('filters', () => {
  it('are the API’s own queries', () => {
    expect(queryFor('all')).toEqual({});
    expect(queryFor('settled')).toEqual({ outcome: 'resolved_by_agent' });
    expect(queryFor('joined')).toEqual({ humanJoined: true });
    expect(queryFor('through')).toEqual({ outcome: 'passed_through' });
    expect(queryFor('refused')).toEqual({ outcome: 'rejected_by_rule' });
  });
});

describe('time', () => {
  it('says seconds under a minute and rounds minutes', () => {
    expect(durationParts(null)).toBeNull();
    expect(durationParts(41.4)).toEqual({ key: 'seconds', count: 41 });
    expect(durationParts(192)).toEqual({ key: 'minutes', count: 3 });
    expect(durationParts(-5)).toEqual({ key: 'seconds', count: 0 });
  });

  it('groups calls under today, yesterday and their date, keeping their order', () => {
    const at = (day: number, hour: number) =>
      new Date(2026, 8, day, hour).toISOString();
    const sections = byDay(
      [
        { started_at: at(13, 10) },
        { started_at: at(13, 9) },
        { started_at: at(12, 19) },
        { started_at: at(9, 8) },
      ],
      NOW,
    );
    expect(
      sections.map(section => [section.day.kind, section.calls.length]),
    ).toEqual([
      ['today', 2],
      ['yesterday', 1],
      ['date', 1],
    ]);
  });

  it('reads clock times, dates and offsets the way the design writes them', () => {
    expect(timeOfDay(new Date(2026, 8, 13, 9, 5).toISOString())).toBe('09:05');
    expect(dayAndMonth('2026-09-19T10:00:00Z', 'en')).toBe('19 September');
    expect(offsetFrom('2026-09-13T09:12:00Z', '2026-09-13T09:13:40Z')).toBe(
      '+1:40',
    );
    expect(offsetFrom('2026-09-13T09:12:00Z', '2026-09-13T09:11:00Z')).toBe(
      '+0:00',
    );
  });

  it('has a word for every outcome, intent and importance', () => {
    expect(Object.keys(en.call.outcome)).toHaveLength(7);
    expect(Object.keys(en.call.intent)).toHaveLength(8);
    expect(Object.keys(en.call.importance)).toEqual([
      '10',
      '20',
      '30',
      '40',
      '50',
    ]);
  });
});

describe('the screenshot guard', () => {
  it('secures on the first holder, unsecures after the last, and ignores a second release', () => {
    const native = { setSecure: jest.fn() };
    const window = secureWindow(() => native);

    const list = window.hold();
    const summary = window.hold();
    expect(native.setSecure.mock.calls).toEqual([[true]]);

    summary();
    summary();
    expect(native.setSecure.mock.calls).toEqual([[true]]);

    list();
    expect(native.setSecure.mock.calls).toEqual([[true], [false]]);
  });

  it('does nothing where there is no module to call', () => {
    const release = secureWindow(() => null).hold();
    expect(release).not.toThrow();
  });
});
