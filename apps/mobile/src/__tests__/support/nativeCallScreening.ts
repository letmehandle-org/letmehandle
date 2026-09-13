/** A fake native call screening module that answers as `CallScreeningModule.kt` does. */
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
  /** Whether calls are being recorded: from starting for an account until it is forgotten. */
  recording = false;
  forgotten = 0;
  requests = 0;
  /** Every account-changing call as it completed, in order: `start`, `write` or `forget`. */
  readonly completed: string[] = [];
  private held: Promise<void> | null = null;
  private releaseHeld: (() => void) | null = null;
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

  /** Keeps every snapshot write unfinished until `releaseSnapshots` is called. */
  holdSnapshots(): void {
    this.held = new Promise(resolve => {
      this.releaseHeld = resolve;
    });
  }

  releaseSnapshots(): void {
    this.releaseHeld?.();
    this.held = null;
  }

  async writeRulesSnapshot(snapshot: string): Promise<void> {
    await this.held;
    if (this.refuseNextSnapshot) {
      this.refuseNextSnapshot = false;
      throw new Error('invalid_snapshot');
    }
    this.snapshots.push(snapshot);
    this.completed.push('write');
  }

  async startRecordingCalls(): Promise<void> {
    this.recording = true;
    this.completed.push('start');
  }

  async forgetAccount(): Promise<void> {
    this.forgotten += 1;
    this.recording = false;
    this.snapshots.length = 0;
    this.pending = [];
    this.completed.push('forget');
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

  /** A call on the handset: recorded and announced, unless no account is signed in. */
  record(...events: Record<string, unknown>[]): void {
    if (!this.recording) {
      return;
    }
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
