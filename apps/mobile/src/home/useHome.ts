/**
 * Today, for Home: loaded when Home opens, again whenever the app comes to the front, and every
 * thirty seconds while it is showing.
 *
 * The last snapshot is kept for as long as the app runs, so switching tabs does not flash an empty
 * ring and a lost connection shows what was there, said to be old, rather than nothing. It is kept
 * against the account it was loaded for, so the next person to sign in never sees it.
 */
import { useCallback, useEffect, useState } from 'react';
import { AppState } from 'react-native';

import type { CallSummary, Escalation } from '@letmehandle/api-client';

import type { ApiClient } from '../api/client';
import type { CallScreening, RoleStatus } from '../calls/callScreening';
import { startOfToday } from './today';

export const REFRESH_EVERY_MS = 30_000;
/** Today's pages read at most. Three hundred calls in a day is past any person's phone. */
const MAX_PAGES = 3;

export interface HomeSnapshot {
  readonly at: Date;
  readonly today: readonly CallSummary[];
  readonly latest: readonly CallSummary[];
  readonly escalation: {
    readonly callId: string;
    readonly detail: Escalation | null;
  } | null;
  readonly screeningRole: RoleStatus | 'failed' | null;
}

let remembered: {
  readonly owner: string;
  readonly snapshot: HomeSnapshot;
} | null = null;

/** Forget every snapshot. For tests, which each start as a fresh app. */
export function forgetHome(): void {
  remembered = null;
}

async function load(
  api: ApiClient,
  screening: CallScreening | null,
  now: Date,
): Promise<HomeSnapshot> {
  const from = startOfToday(now);
  const today: CallSummary[] = [];
  let cursor: string | null = null;
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const found = await api.calls({ from, limit: 100, cursor });
    today.push(...found.calls);
    cursor = found.next_cursor;
    if (cursor === null) {
      break;
    }
  }
  const latest =
    today.length > 0 ? today : (await api.calls({ limit: 3 })).calls;

  let escalation: HomeSnapshot['escalation'] = null;
  for (const call of latest.filter(entry => entry.status === 'in_progress')) {
    const detail = await api.call(call.id);
    if (detail.timings.escalated_at !== null && !detail.human_joined) {
      // Why it needs the user is worth showing, but a Home that fails because it could not be
      // read would hide the one thing that matters: that a call is waiting.
      const context = await api.escalation(call.id).catch(() => null);
      escalation = { callId: call.id, detail: context };
      break;
    }
  }

  let screeningRole: HomeSnapshot['screeningRole'] = null;
  if (screening !== null) {
    screeningRole = await screening.roleStatus().catch(() => 'failed' as const);
  }

  return {
    at: now,
    today,
    latest: latest.slice(0, 3),
    escalation,
    screeningRole,
  };
}

export interface HomeData {
  readonly snapshot: HomeSnapshot | null;
  /** The last refresh failed; `snapshot`, if any, is from before it. */
  readonly stale: boolean;
  readonly failure: unknown;
  refresh(): void;
}

export function useHome(
  api: ApiClient,
  screening: CallScreening | null,
  owner: string,
): HomeData {
  const [snapshot, setSnapshot] = useState<HomeSnapshot | null>(
    remembered?.owner === owner ? remembered.snapshot : null,
  );
  const [failure, setFailure] = useState<unknown>(null);
  const [attempt, setAttempt] = useState(0);

  const refresh = useCallback(() => {
    setAttempt(value => value + 1);
  }, []);

  useEffect(() => {
    let current = true;
    load(api, screening, new Date())
      .then(loaded => {
        if (current) {
          remembered = { owner, snapshot: loaded };
          setSnapshot(loaded);
          setFailure(null);
        }
      })
      .catch((error: unknown) => {
        if (current) {
          setFailure(error);
        }
      });
    return () => {
      current = false;
    };
  }, [api, screening, owner, attempt]);

  useEffect(() => {
    const timer = setInterval(refresh, REFRESH_EVERY_MS);
    const subscription = AppState.addEventListener('change', state => {
      if (state === 'active') {
        refresh();
      }
    });
    return () => {
      clearInterval(timer);
      subscription.remove();
    };
  }, [refresh]);

  return { snapshot, stale: failure !== null, failure, refresh };
}
