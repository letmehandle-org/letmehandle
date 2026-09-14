/** How a call is drawn: its icon, colour, grouping and times, decided in one place. */
import type {
  Caller,
  CallOutcome,
  CallerCategory,
  CallSummary,
} from '@letmehandle/api-client';

import type { Tone } from '../components/Disc';
import type { IconName } from '../components/icon/Icon';
import { localMidnight, minutesAndSeconds } from '../time/clock';

/** What kind of caller, as a picture. */
export const CATEGORY_ICONS: Record<CallerCategory, IconName> = {
  known_contact: 'user',
  delivery: 'truck',
  healthcare: 'health',
  education: 'school',
  financial: 'card',
  service_provider: 'tag',
  sales: 'tag',
  spam: 'ban',
  unknown: 'help',
};

/** The colour a call is drawn in: violet settled, apricot involved the user, neutral otherwise. */
export function toneOf(
  call: Pick<CallSummary, 'outcome' | 'status' | 'human_joined'>,
): Tone {
  if (call.status === 'in_progress' || call.human_joined) {
    return 'needsYou';
  }
  switch (call.outcome) {
    case 'resolved_by_agent':
    case 'passed_through':
      return 'assistant';
    case 'handed_to_user':
    case 'unanswered_escalation':
      return 'needsYou';
    default:
      return 'quiet';
  }
}

export function iconOf(caller: Caller, outcome: CallOutcome | null): IconName {
  return outcome === 'rejected_by_rule'
    ? 'ban'
    : CATEGORY_ICONS[caller.category];
}

/** The outcomes a list can be narrowed to, in the order the filters are offered. */
export const FILTERS = [
  'all',
  'settled',
  'joined',
  'through',
  'refused',
] as const;
export type Filter = (typeof FILTERS)[number];

export function queryFor(filter: Filter): {
  outcome?: CallOutcome;
  humanJoined?: boolean;
} {
  switch (filter) {
    case 'settled':
      return { outcome: 'resolved_by_agent' };
    case 'joined':
      return { humanJoined: true };
    case 'through':
      return { outcome: 'passed_through' };
    case 'refused':
      return { outcome: 'rejected_by_rule' };
    default:
      return {};
  }
}

/** Minutes, or seconds under one: "3 min", "41 s". Nothing for a call with no length yet. */
export function durationParts(
  seconds: number | null,
): { key: 'minutes' | 'seconds'; count: number } | null {
  if (seconds === null) {
    return null;
  }
  const whole = Math.max(0, Math.round(seconds));
  return whole < 60
    ? { key: 'seconds', count: whole }
    : { key: 'minutes', count: Math.round(whole / 60) };
}

/** Which heading a call is listed under: today, yesterday, or its date. */
export function dayOf(
  startedAt: string,
  now: Date,
): { kind: 'today' | 'yesterday' } | { kind: 'date'; date: Date } {
  const midnight = localMidnight(now);
  const startedMidnight = localMidnight(new Date(startedAt));
  const days = Math.round(
    (midnight.getTime() - startedMidnight.getTime()) / 86_400_000,
  );
  if (days <= 0) {
    return { kind: 'today' };
  }
  if (days === 1) {
    return { kind: 'yesterday' };
  }
  return { kind: 'date', date: startedMidnight };
}

export interface Section<T> {
  readonly key: string;
  readonly day: ReturnType<typeof dayOf>;
  readonly calls: readonly T[];
}

/** Calls grouped under their day, keeping the order they arrived in (newest first). */
export function byDay<T extends { readonly started_at: string }>(
  calls: readonly T[],
  now: Date,
): Section<T>[] {
  const sections: { key: string; day: ReturnType<typeof dayOf>; calls: T[] }[] =
    [];
  for (const call of calls) {
    const day = dayOf(call.started_at, now);
    const key = day.kind === 'date' ? day.date.toISOString() : day.kind;
    const last = sections[sections.length - 1];
    if (last?.key === key) {
      last.calls.push(call);
    } else {
      sections.push({ key, day, calls: [call] });
    }
  }
  return sections;
}

/** "10:24", in the phone's own clock. */
export function timeOfDay(instant: string): string {
  const date = new Date(instant);
  return `${String(date.getHours()).padStart(2, '0')}:${String(
    date.getMinutes(),
  ).padStart(2, '0')}`;
}

/** "19 September" in the phone's calendar; a bare `en` is read as British English (D-017). */
export function dayAndMonth(instant: string | Date, locale: string): string {
  return new Date(instant).toLocaleDateString(
    locale === 'en' ? 'en-GB' : locale,
    {
      day: 'numeric',
      month: 'long',
    },
  );
}

/** Offsets from the start, as the "you joined" strip reads them: "+1:40". */
export function offsetFrom(start: string, instant: string): string {
  return `+${minutesAndSeconds(
    Math.round(
      (new Date(instant).getTime() - new Date(start).getTime()) / 1000,
    ),
  )}`;
}

/** The kinds the backend files a call's details under, each of which has its own words. */
export const DETAIL_KINDS = [
  'time',
  'name',
  'reference_number',
  'address',
  'amount',
  'commitment_made',
  'commitment_declined',
  'message',
] as const;

export type DetailKind = (typeof DETAIL_KINDS)[number];

export function isDetailKind(label: string): label is DetailKind {
  return (DETAIL_KINDS as readonly string[]).includes(label);
}
