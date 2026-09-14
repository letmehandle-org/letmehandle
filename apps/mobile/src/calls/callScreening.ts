/** The handset's call screening as typed unions, or null where the platform has none. */
import type { EventSubscription } from 'react-native';

import NativeCallScreening, { type Spec } from './native/NativeCallScreening';
import type { RulesSnapshot } from './wire';

/** Whether this app holds the role, could ask for it, or cannot have it on this handset. */
export type RoleStatus = 'held' | 'available' | 'unavailable';

/** What came of asking. */
export type RoleRequestOutcome = 'held' | 'declined' | 'unavailable';

export interface CallScreening {
  roleStatus(): Promise<RoleStatus>;
  requestRole(): Promise<RoleRequestOutcome>;
  writeRulesSnapshot(snapshot: RulesSnapshot): Promise<void>;
  startRecordingCalls(): Promise<void>;
  forgetAccount(): Promise<void>;
  /** The unreported events, as the JSON array the native side holds. */
  pendingCallEvents(): Promise<string>;
  acknowledgeCallEvents(eventIds: readonly string[]): Promise<void>;
  onCallEventsPending(listener: () => void): EventSubscription;
}

const ROLE_STATUSES: readonly RoleStatus[] = [
  'held',
  'available',
  'unavailable',
];
const ROLE_OUTCOMES: readonly RoleRequestOutcome[] = [
  'held',
  'declined',
  'unavailable',
];

export function callScreeningFrom(
  native: Spec | null | undefined,
): CallScreening | null {
  if (native === null || native === undefined) {
    return null;
  }
  // Account changes and rule writes reach the handset one at a time, in the order they were asked.
  let settled: Promise<void> = Promise.resolve();
  const inOrder = (step: () => Promise<void>): Promise<void> => {
    const done = settled.then(step);
    settled = done.catch(() => undefined);
    return done;
  };
  // Rules are written only between an account starting to record and being forgotten (D-028).
  let signedIn = false;
  return {
    roleStatus: async () => known(await native.roleStatus(), ROLE_STATUSES),
    requestRole: async () => known(await native.requestRole(), ROLE_OUTCOMES),
    writeRulesSnapshot: snapshot =>
      signedIn
        ? inOrder(() => native.writeRulesSnapshot(JSON.stringify(snapshot)))
        : Promise.resolve(),
    startRecordingCalls: () => {
      signedIn = true;
      return inOrder(() => native.startRecordingCalls());
    },
    forgetAccount: () => {
      signedIn = false;
      return inOrder(() => native.forgetAccount());
    },
    pendingCallEvents: () => native.pendingCallEvents(),
    acknowledgeCallEvents: eventIds =>
      native.acknowledgeCallEvents([...eventIds]),
    onCallEventsPending: listener => native.onCallEventsPending(listener),
  };
}

function known<T extends string>(value: string, allowed: readonly T[]): T {
  const found = allowed.find(candidate => candidate === value);
  if (found === undefined) {
    throw new Error(
      `call screening answered "${value}", which this app does not know`,
    );
  }
  return found;
}

/** This handset's call screening, or null where there is none. */
export const callScreening: CallScreening | null =
  callScreeningFrom(NativeCallScreening);
