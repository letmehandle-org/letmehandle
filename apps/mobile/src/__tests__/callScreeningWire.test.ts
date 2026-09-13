/**
 * The documents that cross into and out of the native screening service.
 *
 * Tested against `wire-examples.json`, which the Kotlin codecs' tests read too. The snapshot this
 * side builds must be one the handset reads; the events the handset writes must be ones this
 * side forwards. Both directions are held by the one file.
 */
import type { Preferences } from '@letmehandle/api-client';

import { callScreeningFrom } from '../calls/callScreening';
import { buildRulesSnapshot, parseCallReports } from '../calls/wire';
import examples from '../calls/wire-examples.json';
import { DEFAULT_PREFERENCES } from './support/backend';
import { FakeNativeCallScreening } from './support/nativeCallScreening';

const SYNCED_AT = new Date('2026-09-13T11:00:00Z');

describe('the rules snapshot', () => {
  it('is the document the handset reads, built from the preferences', () => {
    const preferences: Preferences = {
      ...DEFAULT_PREFERENCES,
      call_handling: {
        ...DEFAULT_PREFERENCES.call_handling,
        default_posture: 'handle_with_agent',
        anonymous_posture: 'reject',
        posture_by_category: { unknown: 'pass_through' },
        blocked_categories: ['spam'],
      },
      hours: {
        active: { start: '09:00', end: '17:30', zone: 'Europe/London' },
      },
      important_contacts: [
        {
          phone_number: '+12025550143',
          label: 'The school',
          posture: 'pass_through',
        },
      ],
    };

    expect(buildRulesSnapshot(preferences, SYNCED_AT)).toEqual(
      examples.snapshots.readable[0],
    );
  });

  it('carries no contact labels and no hours', () => {
    // The handset needs a number and what to do with it. Anything else is a copy nothing reads.
    const snapshot = JSON.stringify(
      buildRulesSnapshot(
        {
          ...DEFAULT_PREFERENCES,
          important_contacts: [
            {
              phone_number: '+12025550143',
              label: 'The school',
              posture: 'reject',
            },
          ],
        },
        SYNCED_AT,
      ),
    );
    expect(snapshot).not.toContain('The school');
    expect(snapshot).not.toContain('hours');
  });

  it('is built from call handling alone', () => {
    expect(
      buildRulesSnapshot(
        { ...DEFAULT_PREFERENCES, call_handling: examplesHandling() },
        SYNCED_AT,
      ),
    ).toEqual(examples.snapshots.readable[1]);
  });
});

function examplesHandling(): Preferences['call_handling'] {
  return {
    default_posture: 'pass_through',
    anonymous_posture: 'pass_through',
    escalate_at_or_above: 40,
  };
}

describe('the call reports the handset writes', () => {
  it('reads every example exactly as the backend will receive it', () => {
    expect(parseCallReports(JSON.stringify(examples.events))).toEqual({
      reports: examples.events,
      unreadable: [],
    });
  });

  it('drops a document that is not JSON whole, naming the failure and nothing it held', () => {
    expect(parseCallReports('[{"caller_number":"+12025550145"')).toEqual({
      reports: [],
      unreadable: [{ eventId: null, failure: 'MalformedCallReports' }],
    });
  });

  it.each([
    ['first', 0],
    ['in the middle', 1],
    ['last', 2],
  ])('drops a corrupt entry %s and reads the rest', (_, position) => {
    const entries: unknown[] = examples.events.slice(0, 2);
    entries.splice(position, 0, { event_id: 'corrupt', kind: 'vanished' });

    const read = parseCallReports(JSON.stringify(entries));

    expect(read.reports).toEqual(examples.events.slice(0, 2));
    expect(read.unreadable).toEqual([
      { eventId: 'corrupt', failure: 'MalformedCallReports' },
    ]);
  });

  it.each([
    ['text that is not JSON', 'reports'],
    ['a document that is not a list', '{}'],
    ['an entry that is not an object', '[1]'],
    ['an entry with no event id', '[{"call_id":"c","kind":"incoming"}]'],
    [
      'a kind the backend does not accept',
      '[{"event_id":"e","call_id":"c","kind":"participant_joined","occurred_at":"t"}]',
    ],
    [
      'a decision the backend does not accept',
      '[{"event_id":"e","call_id":"c","kind":"incoming","occurred_at":"t","screening":"maybe"}]',
    ],
    [
      'an ending the backend does not accept',
      '[{"event_id":"e","call_id":"c","kind":"ended","occurred_at":"t","ending":"vanished"}]',
    ],
    [
      'an empty caller number',
      '[{"event_id":"e","call_id":"c","kind":"incoming","occurred_at":"t","caller_number":""}]',
    ],
  ])('drops %s', (_, document) => {
    const read = parseCallReports(document);
    expect(read.reports).toEqual([]);
    expect(read.unreadable).toHaveLength(1);
  });
});

describe('the typed native module', () => {
  it('is absent where the handset has no screening service', () => {
    expect(callScreeningFrom(null)).toBeNull();
    expect(callScreeningFrom(undefined)).toBeNull();
  });

  it('refuses an answer this app does not know rather than guessing', async () => {
    const native = new FakeNativeCallScreening();
    native.role = 'sometimes';
    await expect(callScreeningFrom(native)?.roleStatus()).rejects.toThrow(
      /does not know/,
    );
  });

  it('sends the snapshot as the text the handset reads', async () => {
    const native = new FakeNativeCallScreening();
    const snapshot = buildRulesSnapshot(DEFAULT_PREFERENCES, SYNCED_AT);
    const screening = callScreeningFrom(native);
    await screening?.startRecordingCalls();
    await screening?.writeRulesSnapshot(snapshot);
    expect(native.latestSnapshot()).toEqual(snapshot);
  });
});

describe('the handset account', () => {
  it('writes no rules once forgetting the account has begun', async () => {
    const native = new FakeNativeCallScreening();
    const screening = callScreeningFrom(native);
    const snapshot = buildRulesSnapshot(DEFAULT_PREFERENCES, SYNCED_AT);
    await screening?.startRecordingCalls();
    native.holdSnapshots();

    const inFlight = screening?.writeRulesSnapshot(snapshot);
    const forgetting = screening?.forgetAccount();
    const late = screening?.writeRulesSnapshot(snapshot);
    native.releaseSnapshots();
    await Promise.all([inFlight, forgetting, late]);

    expect(native.completed).toEqual(['start', 'write', 'forget']);
    expect(native.snapshots).toEqual([]);
  });

  it('writes no rules before an account starts recording', async () => {
    const native = new FakeNativeCallScreening();
    await callScreeningFrom(native)?.writeRulesSnapshot(
      buildRulesSnapshot(DEFAULT_PREFERENCES, SYNCED_AT),
    );
    expect(native.completed).toEqual([]);
  });

  it('writes rules again once an account starts recording', async () => {
    const native = new FakeNativeCallScreening();
    const screening = callScreeningFrom(native);
    const snapshot = buildRulesSnapshot(DEFAULT_PREFERENCES, SYNCED_AT);

    await screening?.forgetAccount();
    await screening?.startRecordingCalls();
    await screening?.writeRulesSnapshot(snapshot);

    expect(native.completed).toEqual(['forget', 'start', 'write']);
  });
});
