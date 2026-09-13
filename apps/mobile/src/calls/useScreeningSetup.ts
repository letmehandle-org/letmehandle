/** The call-screening role and the phone-state permission: where each stands, and asking for them. */
import { useCallback, useEffect, useState } from 'react';
import { PermissionsAndroid } from 'react-native';

import type { CallScreening, RoleStatus } from './callScreening';

export type RoleState =
  | { readonly status: 'checking' }
  | { readonly status: 'held' }
  | { readonly status: 'available'; readonly declined: boolean }
  | { readonly status: 'unavailable' }
  | { readonly status: 'failed' };

export type CallActivityState = 'checking' | 'granted' | 'not-granted';

export interface ScreeningSetup {
  readonly role: RoleState;
  readonly callActivity: CallActivityState;
  requestRole(): Promise<void>;
  requestCallActivity(): Promise<void>;
}

const PHONE_STATE = PermissionsAndroid.PERMISSIONS.READ_PHONE_STATE;

function fromStatus(status: RoleStatus): RoleState {
  return status === 'available'
    ? { status: 'available', declined: false }
    : { status };
}

export function useScreeningSetup(
  screening: CallScreening,
  rationale: { title: string; message: string; accept: string },
): ScreeningSetup {
  const [role, setRole] = useState<RoleState>({ status: 'checking' });
  const [callActivity, setCallActivity] =
    useState<CallActivityState>('checking');

  useEffect(() => {
    let cancelled = false;
    Promise.all([screening.roleStatus(), PermissionsAndroid.check(PHONE_STATE)])
      .then(([status, granted]) => {
        if (!cancelled) {
          setRole(fromStatus(status));
          setCallActivity(granted ? 'granted' : 'not-granted');
        }
      })
      .catch(() => {
        // An unknown or missing answer shows as could-not-tell.
        if (!cancelled) {
          setRole({ status: 'failed' });
          setCallActivity('not-granted');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [screening]);

  const requestRole = useCallback(async (): Promise<void> => {
    try {
      const outcome = await screening.requestRole();
      setRole(
        outcome === 'declined'
          ? { status: 'available', declined: true }
          : { status: outcome },
      );
    } catch {
      setRole({ status: 'failed' });
    }
  }, [screening]);

  const requestCallActivity = useCallback(async (): Promise<void> => {
    try {
      const result = await PermissionsAndroid.request(PHONE_STATE, {
        title: rationale.title,
        message: rationale.message,
        buttonPositive: rationale.accept,
      });
      setCallActivity(
        result === PermissionsAndroid.RESULTS.GRANTED
          ? 'granted'
          : 'not-granted',
      );
    } catch {
      // Asked from a screen that has gone: nothing was granted.
      setCallActivity('not-granted');
    }
  }, [rationale.title, rationale.message, rationale.accept]);

  return { role, callActivity, requestRole, requestCallActivity };
}
