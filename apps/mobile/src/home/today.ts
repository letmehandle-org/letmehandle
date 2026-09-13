/**
 * What Home says, decided from what the app knows and nothing else.
 *
 * Pure, so each state the design draws is reached only by the facts that make it true: "on duty"
 * only once calls are arriving, "not on duty" only where this phone has been seen not to screen,
 * and "needs you" only for a call that is still going and has been escalated.
 */
import type { CallSummary } from '@letmehandle/api-client';

import type { RoleStatus } from '../calls/callScreening';

export interface Tally {
  readonly total: number;
  /** Settled by the assistant. */
  readonly handled: number;
  /** Put through to the user, or needing them. */
  readonly you: number;
  /** Refused by a rule before it rang. */
  readonly turnedAway: number;
}

/** Local midnight today, as an instant the API can compare. */
export function startOfToday(now: Date): string {
  return new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate(),
  ).toISOString();
}

export function tally(calls: readonly CallSummary[]): Tally {
  let handled = 0;
  let you = 0;
  let turnedAway = 0;
  for (const call of calls) {
    if (call.outcome === 'rejected_by_rule') {
      turnedAway += 1;
    } else if (
      call.human_joined ||
      call.outcome === 'handed_to_user' ||
      call.outcome === 'passed_through' ||
      call.outcome === 'unanswered_escalation'
    ) {
      you += 1;
    } else if (call.outcome === 'resolved_by_agent') {
      handled += 1;
    }
  }
  return { total: calls.length, handled, you, turnedAway };
}

export type HomeState =
  | 'needs-you'
  | 'not-on-duty'
  | 'screening'
  | 'on-duty'
  | 'first-day';

export interface Facts {
  /** A call still going that has been escalated, if there is one. */
  readonly escalatedCallId: string | null;
  /** This phone's call screening role, or null where it has no screening at all. */
  readonly screeningRole: RoleStatus | 'failed' | null;
  /** Whether any call has ever reached history. */
  readonly anyCalls: boolean;
}

export function homeState(facts: Facts): HomeState {
  if (facts.escalatedCallId !== null) {
    return 'needs-you';
  }
  if (facts.screeningRole !== null) {
    return facts.screeningRole === 'held' ? 'screening' : 'not-on-duty';
  }
  return facts.anyCalls ? 'on-duty' : 'first-day';
}

/** How far through the ring each kind of call goes, in the ring's drawing order. */
export function ringParts(counts: Tally): {
  handled: number;
  you: number;
  turnedAway: number;
} {
  if (counts.total === 0) {
    return { handled: 0, you: 0, turnedAway: 0 };
  }
  return {
    handled: counts.handled / counts.total,
    you: counts.you / counts.total,
    turnedAway: counts.turnedAway / counts.total,
  };
}

/** "1:28", minutes and seconds since the escalation was raised. */
export function elapsed(since: string, now: Date): string {
  const seconds = Math.max(
    0,
    Math.floor((now.getTime() - new Date(since).getTime()) / 1000),
  );
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}
