/**
 * Where this handset stands on call screening, and asking for what it needs.
 *
 * Two separate grants, and the difference matters to what the user is told. The call-screening
 * role is what lets the app decide a call before it rings; without it nothing is screened and
 * every call rings as it would anyway. The phone-state permission only lets the app see that a
 * call was answered or ended; without it screening still works and less is reported.
 *
 * Declining either is an answer, not an error. It is remembered for the screen so that it can
 * say what is now true, and the user can ask again whenever they like.
 */
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
        // The native side answered with something this app does not know, or not at all. The
        // screen says it could not tell rather than guessing either way.
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
