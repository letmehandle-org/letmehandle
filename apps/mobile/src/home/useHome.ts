/** Today's calls for Home, refreshed on focus and on an interval, remembered per account. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { AppState } from 'react-native';

import type { CallSummary, Escalation } from '@letmehandle/api-client';

import type { ApiClient } from '../api/client';
import type { CallScreening, RoleStatus } from '../calls/callScreening';
import { startOfToday } from './today';

export const REFRESH_EVERY_MS = 30_000;
/** The most pages of today's calls read. */
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

/** Forgets every remembered snapshot. */
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
      // An escalation whose context cannot be read still shows as waiting.
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
  owner: string | null,
): HomeData {
  const [snapshot, setSnapshot] = useState<HomeSnapshot | null>(
    owner !== null && remembered?.owner === owner ? remembered.snapshot : null,
  );
  const [failure, setFailure] = useState<unknown>(null);
  const [attempt, setAttempt] = useState(0);
  const loading = useRef(false);

  const refresh = useCallback(() => {
    setAttempt(value => value + 1);
  }, []);

  useEffect(() => {
    let current = true;
    loading.current = true;
    load(api, screening, new Date())
      .then(loaded => {
        if (current) {
          if (owner !== null) {
            remembered = { owner, snapshot: loaded };
          }
          setSnapshot(loaded);
          setFailure(null);
        }
      })
      .catch((error: unknown) => {
        if (current) {
          setFailure(error);
        }
      })
      .finally(() => {
        if (current) {
          loading.current = false;
        }
      });
    return () => {
      current = false;
    };
  }, [api, screening, owner, attempt]);

  useEffect(() => {
    // A load still under way is left to finish rather than started again.
    const whenIdle = (): void => {
      if (!loading.current) {
        refresh();
      }
    };
    const timer = setInterval(whenIdle, REFRESH_EVERY_MS);
    const subscription = AppState.addEventListener('change', state => {
      if (state === 'active') {
        whenIdle();
      }
    });
    return () => {
      clearInterval(timer);
      subscription.remove();
    };
  }, [refresh]);

  return { snapshot, stale: failure !== null, failure, refresh };
}
