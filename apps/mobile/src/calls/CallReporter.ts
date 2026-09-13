/**
 * Sending what the handset observed about its calls to the backend.
 *
 * The native side records events whether or not this JavaScript is running — a call can arrive
 * while the app has not been opened since the phone started. This drains that record whenever the
 * app is running: at start, when it comes back to the front, and when the native side says
 * something new is waiting.
 *
 * An event is forgotten only once the backend has answered for it: stored now, stored before, or
 * refused. A refused report is refused for what it says, so sending it again would be refused
 * again, and keeping it would put it at the front of every later request. Anything else — no
 * connection, a server fault — leaves the event where it is. That is tried again a few times, a
 * little later each time, and then left for the next drain; the backend's idempotency makes
 * sending it twice harmless.
 *
 * An entry the handset holds that cannot be read at all is forgotten, and said by its kind of
 * failure only. Kept, it would be read and dropped again on every drain.
 */
import type {
  CallReport,
  CallReportBatch,
  CallReportReceipt,
} from '@letmehandle/api-client';

import { ApiError, NetworkError } from '../api/errors';
import type { CallScreening } from './callScreening';
import { parseCallReports, type UnreadableCallReport } from './wire';

/** The most the backend accepts in one request (`MAX_REPORTS_PER_REQUEST`). */
export const REPORTS_PER_REQUEST = 100;

/** How many times one request is sent before the drain gives up on it. */
export const SEND_ATTEMPTS = 3;

/** The wait before the first retry. Each one after waits twice as long as the last. */
export const FIRST_RETRY_DELAY_MS = 1_000;

export interface CallReportSender {
  reportCalls(batch: CallReportBatch): Promise<CallReportReceipt>;
}

export type Wait = (milliseconds: number) => Promise<void>;

const pause: Wait = milliseconds =>
  new Promise(resolve => {
    setTimeout(resolve, milliseconds);
  });

/** Told the kind of failure of each entry a drain dropped. Never the entries: they hold numbers. */
export type UnreadableListener = (failures: readonly string[]) => void;

const warnUnreadable: UnreadableListener = failures => {
  const kinds = [...new Set(failures)].join(', ');
  console.warn(`dropped ${failures.length} unreadable call reports: ${kinds}`);
};

export class CallReporter {
  private readonly screening: CallScreening;
  private readonly sender: CallReportSender;
  private readonly wait: Wait;
  private readonly onUnreadable: UnreadableListener;
  /** The drain in flight, shared by everything that asks for one while it runs. */
  private running: Promise<void> | null = null;
  /** Whether another drain was asked for while one was running. */
  private again = false;

  constructor(
    screening: CallScreening,
    sender: CallReportSender,
    wait: Wait = pause,
    onUnreadable: UnreadableListener = warnUnreadable,
  ) {
    this.screening = screening;
    this.sender = sender;
    this.wait = wait;
    this.onUnreadable = onUnreadable;
  }

  /**
   * Send everything waiting. One at a time: two drains reading the same record at once would
   * send every event twice, which is harmless and wasteful. A drain asked for mid-flight runs
   * once more afterwards, so an event recorded during a send is not left until the next trigger.
   */
  drain(): Promise<void> {
    if (this.running !== null) {
      this.again = true;
      return this.running;
    }
    this.running = this.drainUntilSettled().finally(() => {
      this.running = null;
    });
    return this.running;
  }

  private async drainUntilSettled(): Promise<void> {
    do {
      this.again = false;
      await this.sendPending();
    } while (this.again);
  }

  private async sendPending(): Promise<void> {
    const { reports: pending, unreadable } = parseCallReports(
      await this.screening.pendingCallEvents(),
    );
    if (unreadable.length > 0) {
      await this.forgetUnreadable(pending, unreadable);
    }
    for (let start = 0; start < pending.length; start += REPORTS_PER_REQUEST) {
      const batch = pending.slice(start, start + REPORTS_PER_REQUEST);
      const outcome = await this.send({ reports: batch });
      await this.screening.acknowledgeCallEvents([
        ...outcome.accepted,
        ...outcome.duplicates,
        // A rejection without a readable id is found by its place in the batch.
        ...outcome.rejected
          .map(
            rejection => rejection.event_id ?? batch[rejection.index]?.event_id,
          )
          .filter((eventId): eventId is string => eventId !== undefined),
      ]);
    }
  }

  /**
   * Forgets entries that will never read, before anything is sent, so a failed send does not
   * leave them to be read and dropped again on every drain.
   */
  private async forgetUnreadable(
    readable: readonly CallReport[],
    unreadable: readonly UnreadableCallReport[],
  ): Promise<void> {
    this.onUnreadable(unreadable.map(entry => entry.failure));
    // An id a readable report also carries is left alone: forgetting it would forget that report.
    const kept = new Set(readable.map(report => report.event_id));
    const forgettable = unreadable.flatMap(({ eventId }) =>
      eventId === null || kept.has(eventId) ? [] : [eventId],
    );
    if (forgettable.length > 0) {
      await this.screening.acknowledgeCallEvents(forgettable);
    }
  }

  private async send(batch: CallReportBatch): Promise<CallReportReceipt> {
    for (let attempt = 1; ; attempt += 1) {
      try {
        return await this.sender.reportCalls(batch);
      } catch (error) {
        if (attempt >= SEND_ATTEMPTS || !isTransient(error)) {
          throw error;
        }
        await this.wait(FIRST_RETRY_DELAY_MS * 2 ** (attempt - 1));
      }
    }
  }
}

/**
 * Whether the same request could succeed shortly: the network did not answer, the server
 * faulted, or it asked for less. Any other refusal — a session that has gone, say — would be the
 * same a second later.
 */
function isTransient(error: unknown): boolean {
  if (error instanceof NetworkError) {
    return true;
  }
  return (
    error instanceof ApiError && (error.status >= 500 || error.status === 429)
  );
}
