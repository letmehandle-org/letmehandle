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
 */
import type {
  CallReportBatch,
  CallReportReceipt,
} from '@letmehandle/api-client';

import { ApiError, NetworkError } from '../api/errors';
import type { CallScreening } from './callScreening';
import { parseCallReports } from './wire';

/** The most the backend accepts in one request (`MAX_REPORTS_PER_REQUEST`). */
export const REPORTS_PER_REQUEST = 100;

/** How many times one request is sent before the drain gives up on it. */
export const SEND_ATTEMPTS = 3;

/** The wait before the first retry. Each one after waits twice as long as the last. */
export const FIRST_RETRY_DELAY_MS = 1_000;

/** A report the backend will not store, and why. */
export interface RejectedReport {
  readonly event_id: string;
  readonly reason: string;
}

/**
 * The backend's answer for a batch.
 *
 * `rejected` is optional until the generated client carries it: a backend that predates it
 * refuses a batch whole instead, which reaches here as an error and keeps every report.
 */
export interface CallReportOutcome extends CallReportReceipt {
  readonly rejected?: readonly RejectedReport[];
}

export interface CallReportSender {
  reportCalls(batch: CallReportBatch): Promise<CallReportOutcome>;
}

export type Wait = (milliseconds: number) => Promise<void>;

const pause: Wait = milliseconds =>
  new Promise(resolve => {
    setTimeout(resolve, milliseconds);
  });

export class CallReporter {
  private readonly screening: CallScreening;
  private readonly sender: CallReportSender;
  private readonly wait: Wait;
  /** The drain in flight, shared by everything that asks for one while it runs. */
  private running: Promise<void> | null = null;
  /** Whether another drain was asked for while one was running. */
  private again = false;

  constructor(
    screening: CallScreening,
    sender: CallReportSender,
    wait: Wait = pause,
  ) {
    this.screening = screening;
    this.sender = sender;
    this.wait = wait;
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
    const pending = parseCallReports(await this.screening.pendingCallEvents());
    for (let start = 0; start < pending.length; start += REPORTS_PER_REQUEST) {
      const batch = pending.slice(start, start + REPORTS_PER_REQUEST);
      const outcome = await this.send({ reports: batch });
      await this.screening.acknowledgeCallEvents([
        ...outcome.accepted,
        ...outcome.duplicates,
        ...(outcome.rejected ?? []).map(rejection => rejection.event_id),
      ]);
    }
  }

  private async send(batch: CallReportBatch): Promise<CallReportOutcome> {
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
