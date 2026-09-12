/**
 * The native call screening module, standing in for the Kotlin one.
 *
 * It keeps what it is told and answers the way `CallScreeningModule.kt` does — including
 * refusing a snapshot it could not read — so a test about the app's behaviour is not a test of a
 * stub that agrees with everything.
 */
import type { EventSubscription } from 'react-native';

import type { Spec } from '../../calls/native/NativeCallScreening';

export class FakeNativeCallScreening implements Spec {
  /** What `roleStatus` answers. */
  role = 'available';
  /** What asking for the role comes to. */
  requestOutcome = 'declined';
  /** Every snapshot written, in order, as the text the native side received. */
  readonly snapshots: string[] = [];
  /** Refuse the next snapshot, as the native codec does with a document it cannot read. */
  refuseNextSnapshot = false;
  /** The events waiting to be reported. */
  pending: Record<string, unknown>[] = [];
  forgotten = 0;
  requests = 0;
  private readonly listeners = new Set<() => void>();

  async roleStatus(): Promise<string> {
    return this.role;
  }

  async requestRole(): Promise<string> {
    this.requests += 1;
    if (this.requestOutcome === 'held') {
      this.role = 'held';
    }
    return this.requestOutcome;
  }

  async writeRulesSnapshot(snapshot: string): Promise<void> {
    if (this.refuseNextSnapshot) {
      this.refuseNextSnapshot = false;
      throw new Error('invalid_snapshot');
    }
    this.snapshots.push(snapshot);
  }

  async forgetAccount(): Promise<void> {
    this.forgotten += 1;
    this.snapshots.length = 0;
    this.pending = [];
  }

  async pendingCallEvents(): Promise<string> {
    return JSON.stringify(this.pending);
  }

  async acknowledgeCallEvents(eventIds: ReadonlyArray<string>): Promise<void> {
    this.pending = this.pending.filter(
      event => !eventIds.includes(String(event.event_id)),
    );
  }

  readonly onCallEventsPending = (
    listener: (value: void) => void | Promise<void>,
  ): EventSubscription => {
    const wrapped = (): void => {
      listener();
    };
    this.listeners.add(wrapped);
    return {
      remove: () => {
        this.listeners.delete(wrapped);
      },
    };
  };

  /** The native side recording a call and saying so. */
  record(...events: Record<string, unknown>[]): void {
    this.pending.push(...events);
    this.listeners.forEach(listener => listener());
  }

  latestSnapshot(): Record<string, unknown> {
    const latest = this.snapshots.at(-1);
    if (latest === undefined) {
      throw new Error('no snapshot has been written');
    }
    return JSON.parse(latest) as Record<string, unknown>;
  }
}
