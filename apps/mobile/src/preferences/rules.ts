/**
 * The design's rules, expressed in the preferences the API has today.
 *
 * The design describes calls with two lanes and one graph rather than with a posture per
 * category, a threshold and five switches. These functions are the translation between the
 * two, kept in one place and tested, so no screen invents its own reading of either.
 */
import type {
  CallHandling,
  Hours,
  Notifications,
  Preferences,
} from '@letmehandle/api-client';

/**
 * Contacts ring the user, everyone else meets the assistant, known spam is turned away.
 *
 * Withheld numbers are unknown numbers, so they go to the assistant too. The escalation threshold
 * has no control in the design and is carried over as it was.
 */
export function twoLanes(current: CallHandling): CallHandling {
  return {
    default_posture: 'handle_with_agent',
    anonymous_posture: 'handle_with_agent',
    posture_by_category: { known_contact: 'pass_through' },
    blocked_categories: ['spam'],
    escalate_at_or_above: current.escalate_at_or_above,
  };
}

/** Whether stored call handling already says exactly what the two lanes say. */
export function followsTwoLanes(current: CallHandling): boolean {
  const expected = twoLanes(current);
  const postures = current.posture_by_category ?? {};
  const blocked = current.blocked_categories ?? [];
  return (
    current.default_posture === expected.default_posture &&
    current.anonymous_posture === expected.anonymous_posture &&
    Object.keys(postures).length === 1 &&
    postures.known_contact === 'pass_through' &&
    blocked.length === 1 &&
    blocked[0] === 'spam'
  );
}

/**
 * "Tell me about every call": handled and turned away, together.
 *
 * On only when both are, because a switch that reads on while half of what it names is off is
 * one people stop trusting.
 */
export function hearsEveryCall(notifications: Notifications): boolean {
  return notifications.on_handled_call && notifications.on_blocked_call;
}

export function withEveryCall(
  notifications: Notifications,
  on: boolean,
): Notifications {
  return { ...notifications, on_handled_call: on, on_blocked_call: on };
}

/** Whether the assistant is set to answer at any hour: no window of either kind. */
export function answersAroundTheClock(hours: Hours): boolean {
  return (hours.working ?? null) === null && (hours.quiet ?? null) === null;
}

export const AROUND_THE_CLOCK: Hours = { working: null, quiet: null };

/** How many of the assistant's capabilities are granted, out of how many exist. */
export function grantedCount(
  preferences: Preferences,
  total: number,
): { granted: number; total: number } {
  return { granted: preferences.authority.capabilities?.length ?? 0, total };
}

/**
 * The whole personality section with one part changed.
 *
 * The API replaces a section it is given, so a change to the topics has to carry the tone,
 * length and facts as they stand, or saving one would clear the others.
 */
export function personalityWith(
  preferences: Preferences,
  change: Partial<Preferences['personality']>,
): Preferences['personality'] {
  const current = preferences.personality;
  return {
    formality: current.formality,
    verbosity: current.verbosity,
    topics: current.topics ?? [],
    disclosable_facts: current.disclosable_facts ?? [],
    ...change,
  };
}

/** The most facts the assistant may hold, and the longest one may be (the API's limits). */
export const MAX_FACTS = 20;
export const MAX_FACT_LENGTH = 120;

/** What is wrong with a fact before it is added, as a translation key, or null. */
export function factProblem(
  raw: string,
  existing: readonly string[],
): string | null {
  const fact = raw.trim();
  if (fact.length === 0 || fact.length > MAX_FACT_LENGTH) {
    return 'say.invalid';
  }
  if (existing.some(entry => entry.toLowerCase() === fact.toLowerCase())) {
    return 'say.duplicate';
  }
  if (existing.length >= MAX_FACTS) {
    return 'say.full';
  }
  return null;
}
