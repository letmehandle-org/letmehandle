/**
 * How a call is drawn, decided once.
 *
 * Every screen that shows a call — the list, the summary, the escalation — reads its icon, its
 * colour and its words from here, so a refused call cannot be grey on one screen and red on
 * another. Pure, so the choices are tested without rendering anything.
 */
import type {
  Caller,
  CallOutcome,
  CallerCategory,
  CallSummary,
} from '@letmehandle/api-client';

import type { Tone } from '../components/Disc';
import type { IconName } from '../components/icon/Icon';

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

/**
 * The colour a call is drawn in.
 *
 * Violet for what the assistant settled, apricot for anything that involved the user or still
 * does, neutral for what was refused or went nowhere. Red is never used: nothing here is being
 * destroyed.
 */
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
  const started = new Date(startedAt);
  const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const startedMidnight = new Date(
    started.getFullYear(),
    started.getMonth(),
    started.getDate(),
  );
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

/**
 * "19 September", in the phone's own calendar.
 *
 * The shipped language is British English (D-017), and a bare `en` would otherwise be read by
 * the formatter as American and put the month first.
 */
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
  const seconds = Math.max(
    0,
    Math.round(
      (new Date(instant).getTime() - new Date(start).getTime()) / 1000,
    ),
  );
  return `+${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(
    2,
    '0',
  )}`;
}
