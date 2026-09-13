/**
 * Call screening on this handset, typed.
 *
 * The generated native interface speaks in strings; this is where they become the unions the
 * rest of the app reads, and where an unexpected one is an error rather than a silent default.
 * The strings are written in `CallScreeningModule.kt`, whose constants carry the same values.
 *
 * `null` where the platform has no screening service. That is a capability, not a platform
 * check: a screen asks whether screening exists, never which operating system it is on.
 */
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
  return {
    roleStatus: async () => known(await native.roleStatus(), ROLE_STATUSES),
    requestRole: async () => known(await native.requestRole(), ROLE_OUTCOMES),
    writeRulesSnapshot: snapshot =>
      native.writeRulesSnapshot(JSON.stringify(snapshot)),
    forgetAccount: () => native.forgetAccount(),
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
