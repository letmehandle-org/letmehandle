/**
 * Sending what the handset observed about its calls to the backend.
 *
 * The native side records events whether or not this JavaScript is running — a call can arrive
 * while the app has not been opened since the phone started. This drains that record whenever the
 * app is running: at start, when it comes back to the front, and when the native side says
 * something new is waiting.
 *
 * An event is forgotten only once the backend has answered for it, as stored now or stored
 * before. Anything else — no connection, a server fault — leaves it where it is for the next
 * drain, and the backend's idempotency makes sending it twice harmless.
 */
import type {
  CallReportBatch,
  CallReportReceipt,
} from '@letmehandle/api-client';

import type { CallScreening } from './callScreening';
import { parseCallReports } from './wire';

/** The most the backend accepts in one request (`MAX_REPORTS_PER_REQUEST`). */
export const REPORTS_PER_REQUEST = 100;

export interface CallReportSender {
  reportCalls(batch: CallReportBatch): Promise<CallReportReceipt>;
}

export class CallReporter {
  private readonly screening: CallScreening;
  private readonly sender: CallReportSender;
  /** The drain in flight, shared by everything that asks for one while it runs. */
  private running: Promise<void> | null = null;
  /** Whether another drain was asked for while one was running. */
  private again = false;

  constructor(screening: CallScreening, sender: CallReportSender) {
    this.screening = screening;
    this.sender = sender;
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
      const receipt = await this.sender.reportCalls({ reports: batch });
      await this.screening.acknowledgeCallEvents([
        ...receipt.accepted,
        ...receipt.duplicates,
      ]);
    }
  }
}
