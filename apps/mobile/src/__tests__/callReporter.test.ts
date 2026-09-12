/**
 * Reporting the handset's calls: nothing forgotten before the backend has answered for it, nothing
 * sent twice at once, nothing lost when the backend is away, and no one report the backend refuses
 * holding back the ones behind it.
 */
import type { CallReportBatch } from '@letmehandle/api-client';

import { ApiError, NetworkError } from '../api/errors';
import {
  CallReporter,
  FIRST_RETRY_DELAY_MS,
  REPORTS_PER_REQUEST,
  SEND_ATTEMPTS,
  type CallReportOutcome,
} from '../calls/CallReporter';
import { callScreeningFrom, type CallScreening } from '../calls/callScreening';
import { FakeNativeCallScreening } from './support/nativeCallScreening';

function event(index: number): Record<string, unknown> {
  return {
    event_id: `event-${String(index).padStart(4, '0')}`,
    call_id: `call-${index}`,
    kind: 'incoming',
    occurred_at: '2026-09-13T11:00:00Z',
    screening: 'allow',
  };
}

function serverFault(): ApiError {
  return new ApiError(503, { error: 'internal_error', message: 'down' });
}

class RecordingSender {
  readonly batches: CallReportBatch[] = [];
  /** What the next requests fail with, in order, before the backend answers again. */
  readonly failures: Error[] = [];
  /** Event ids the backend already has, so it answers for them as duplicates. */
  readonly known = new Set<string>();
  /** Event ids the backend will never store, and says so. */
  readonly refused = new Set<string>();
  private release: (() => void) | null = null;

  hold(): void {
    this.release = null;
    this.held = new Promise(resolve => {
      this.release = resolve;
    });
  }

  held: Promise<void> | null = null;

  letGo(): void {
    this.release?.();
    this.held = null;
  }

  async reportCalls(batch: CallReportBatch): Promise<CallReportOutcome> {
    this.batches.push(batch);
    if (this.held !== null) {
      await this.held;
    }
    const failure = this.failures.shift();
    if (failure !== undefined) {
      throw failure;
    }
    const ids = batch.reports.map(report => report.event_id);
    const stored = ids.filter(id => !this.refused.has(id));
    return {
      accepted: stored.filter(id => !this.known.has(id)),
      duplicates: stored.filter(id => this.known.has(id)),
      rejected: ids
        .filter(id => this.refused.has(id))
        .map(id => ({ event_id: id, reason: 'caller_number is not E.164' })),
    };
  }
}

function setUp(): {
  native: FakeNativeCallScreening;
  sender: RecordingSender;
  reporter: CallReporter;
  waits: number[];
} {
  const native = new FakeNativeCallScreening();
  const screening = callScreeningFrom(native) as CallScreening;
  const sender = new RecordingSender();
  const waits: number[] = [];
  const wait = async (milliseconds: number): Promise<void> => {
    waits.push(milliseconds);
  };
  return {
    native,
    sender,
    waits,
    reporter: new CallReporter(screening, sender, wait),
  };
}

describe('reporting what the handset observed', () => {
  it('sends what is waiting and forgets it once the backend has it', async () => {
    const { native, sender, reporter } = setUp();
    native.pending = [event(1), event(2)];

    await reporter.drain();

    expect(sender.batches).toHaveLength(1);
    expect(sender.batches[0].reports.map(report => report.event_id)).toEqual([
      'event-0001',
      'event-0002',
    ]);
    expect(native.pending).toEqual([]);
  });

  it('forgets what the backend already had, too', async () => {
    const { native, sender, reporter } = setUp();
    native.pending = [event(1), event(2)];
    sender.known.add('event-0001');

    await reporter.drain();

    expect(native.pending).toEqual([]);
  });

  it('forgets a report the backend refuses, so it is not sent again', async () => {
    const { native, sender, reporter } = setUp();
    native.pending = [event(1), event(2)];
    sender.refused.add('event-0001');

    await reporter.drain();
    expect(native.pending).toEqual([]);

    await reporter.drain();
    expect(sender.batches).toHaveLength(1);
  });

  it('does not let a refused report hold back the ones behind it', async () => {
    const { native, sender, reporter } = setUp();
    native.pending = Array.from({ length: REPORTS_PER_REQUEST + 1 }, (_, i) =>
      event(i),
    );
    sender.refused.add('event-0000');

    await reporter.drain();
    native.record(event(500));
    await reporter.drain();

    const sent = sender.batches.flatMap(batch =>
      batch.reports.map(report => report.event_id),
    );
    expect(sent.filter(id => id === 'event-0000')).toHaveLength(1);
    expect(sent).toContain('event-0100');
    expect(sent).toContain('event-0500');
    expect(native.pending).toEqual([]);
  });

  it('tries again, waiting longer each time, when the backend is briefly away', async () => {
    const { native, sender, reporter, waits } = setUp();
    native.pending = [event(1)];
    sender.failures.push(
      new NetworkError(new TypeError('offline')),
      serverFault(),
    );

    await reporter.drain();

    expect(sender.batches).toHaveLength(3);
    expect(waits).toEqual([FIRST_RETRY_DELAY_MS, FIRST_RETRY_DELAY_MS * 2]);
    expect(native.pending).toEqual([]);
  });

  it('gives up after a few tries, keeps everything, and sends it next time', async () => {
    const { native, sender, reporter } = setUp();
    native.pending = [event(1)];
    for (let attempt = 0; attempt < SEND_ATTEMPTS; attempt += 1) {
      sender.failures.push(serverFault());
    }

    await expect(reporter.drain()).rejects.toThrow('down');
    expect(sender.batches).toHaveLength(SEND_ATTEMPTS);
    expect(native.pending).toHaveLength(1);

    await reporter.drain();
    expect(native.pending).toEqual([]);
  });

  it('does not repeat a request the backend refused outright', async () => {
    const { native, sender, reporter, waits } = setUp();
    native.pending = [event(1)];
    sender.failures.push(
      new ApiError(401, { error: 'not_authenticated', message: 'signed out' }),
    );

    await expect(reporter.drain()).rejects.toThrow('signed out');
    expect(sender.batches).toHaveLength(1);
    expect(waits).toEqual([]);
    expect(native.pending).toHaveLength(1);
  });

  it('sends no more in one request than the backend accepts', async () => {
    const { native, sender, reporter } = setUp();
    native.pending = Array.from({ length: REPORTS_PER_REQUEST + 1 }, (_, i) =>
      event(i),
    );

    await reporter.drain();

    expect(sender.batches.map(batch => batch.reports.length)).toEqual([
      REPORTS_PER_REQUEST,
      1,
    ]);
    expect(native.pending).toEqual([]);
  });

  it('sends nothing when nothing is waiting', async () => {
    const { sender, reporter } = setUp();
    await reporter.drain();
    expect(sender.batches).toEqual([]);
  });

  it('runs one drain at a time, and once more for what arrived during it', async () => {
    const { native, sender, reporter } = setUp();
    native.pending = [event(1)];
    sender.hold();

    const first = reporter.drain();
    // Recorded while the first request is still out.
    native.pending.push(event(2));
    const second = reporter.drain();
    expect(second).toBe(first);

    sender.letGo();
    await first;

    const sent = sender.batches.flatMap(batch =>
      batch.reports.map(report => report.event_id),
    );
    expect(sent.filter(id => id === 'event-0001')).toHaveLength(1);
    expect(sent).toContain('event-0002');
    expect(native.pending).toEqual([]);
  });
});
