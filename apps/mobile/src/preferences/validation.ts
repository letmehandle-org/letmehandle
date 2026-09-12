/**
 * What is wrong with a draft, before it is sent.
 *
 * These exist for the feedback, not for the authority. Every rule here is also a rule on the
 * server, and the server is the one that decides — so nothing in this file may be relied on to
 * keep bad data out, and a save the server refuses must still be surfaced. What it buys is the
 * user finding out about a mistyped time while the field is in front of them rather than after
 * a round trip.
 *
 * Each function answers with a translation key rather than a sentence: prose lives in the
 * locale, and a message built here would be a string outside it.
 */
import type { ImportantContact, TimeWindow } from '@letmehandle/api-client';

/** A 24-hour time, which is the only form the backend stores a window in. */
const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;

/** E.164: a plus, a country code that does not start with zero, fifteen digits at the most. */
const E164 = /^\+[1-9]\d{1,14}$/;

/** Everything people type into a phone number that is not part of it. */
const DECORATION = /[\s\-().]/g;

/** The longest a topic may be before it stops being a phrase. */
const MAX_TOPIC = 60;

/**
 * A number in the single form everything else compares against.
 *
 * The same normalisation the backend does, for the same reason: a number stored in two forms is
 * two numbers to anything that compares them, and here that would mean a duplicate the user can
 * see is a duplicate and this cannot.
 */
export function normaliseNumber(raw: string): string {
  const candidate = raw.trim().replace(DECORATION, '');
  return candidate.startsWith('00') ? `+${candidate.slice(2)}` : candidate;
}

/** A topic with its spacing and case settled, so that two spellings are one topic. */
export function normaliseTopic(raw: string): string {
  return raw.split(/\s+/).filter(Boolean).join(' ').toLowerCase();
}

export function timeProblem(value: string): string | null {
  return TIME.test(value) ? null : 'preferences.hours.invalidTime';
}

/**
 * Whether a window describes a period of time at all.
 *
 * A window may run past midnight — ten at night until seven is the ordinary quiet hours — so
 * the end being earlier than the start is not a mistake. The two being equal is: it covers
 * nothing, and the backend refuses it.
 */
export function windowProblem(window: TimeWindow): string | null {
  const start = timeProblem(window.start);
  if (start !== null) {
    return start;
  }
  const end = timeProblem(window.end);
  if (end !== null) {
    return end;
  }
  if (window.start === window.end) {
    return 'preferences.hours.emptyWindow';
  }
  if (window.zone.trim() === '') {
    return 'preferences.hours.invalidZone';
  }
  return null;
}

/**
 * Whether a contact can be added, given the ones already on the list.
 *
 * The duplicate check is here rather than left to the server because the server has no reason
 * to refuse it: a second entry for the same number is valid data and useless data, and the only
 * place that knows it is useless is the screen the user is looking at.
 */
export function contactProblem(
  draft: ImportantContact,
  existing: readonly ImportantContact[],
): string | null {
  if (draft.label.trim() === '' || draft.label.length > 80) {
    return 'preferences.important_contacts.invalidLabel';
  }

  const number = normaliseNumber(draft.phone_number);
  if (!E164.test(number)) {
    return 'preferences.important_contacts.invalidNumber';
  }

  const clash = existing.some(
    contact => normaliseNumber(contact.phone_number) === number,
  );
  return clash ? 'preferences.important_contacts.duplicate' : null;
}

export function topicProblem(
  raw: string,
  existing: readonly string[],
): string | null {
  const topic = normaliseTopic(raw);
  if (topic === '' || topic.length > MAX_TOPIC) {
    return 'preferences.personality.invalidTopic';
  }
  return existing.includes(topic)
    ? 'preferences.personality.duplicateTopic'
    : null;
}
