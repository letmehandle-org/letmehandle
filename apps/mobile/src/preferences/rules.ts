/** The design's lanes and switches, translated to and from the API's preferences. */
import type {
  CallHandling,
  Notifications,
  Preferences,
} from '@letmehandle/api-client';

/** Contacts ring the user, everyone else meets the assistant and spam is refused; the threshold is kept. */
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

/** "Tell me about every call": on only when handled and blocked calls both notify. */
export function hearsEveryCall(notifications: Notifications): boolean {
  return notifications.on_handled_call && notifications.on_blocked_call;
}

export function withEveryCall(
  notifications: Notifications,
  on: boolean,
): Notifications {
  return { ...notifications, on_handled_call: on, on_blocked_call: on };
}

/** The whole personality section with one part changed, since the API replaces a section. */
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
