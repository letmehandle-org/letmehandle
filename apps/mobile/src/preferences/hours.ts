/**
 * The assistant's hours: one daily window in the user's own zone, or none for around the clock.
 *
 * Pure, so the arithmetic the ring draws is tested apart from the drawing. A window may run past
 * midnight, and the end is exclusive — the same reading the server uses (D-029).
 */
import type { TimeWindow } from '@letmehandle/api-client';

const MINUTES_IN_A_DAY = 24 * 60;

/** Every half hour of the day, which is as fine as the chooser offers. */
export const TIMES: readonly string[] = Array.from(
  { length: 48 },
  (_, slot) =>
    `${String(Math.floor(slot / 2)).padStart(2, '0')}:${
      slot % 2 === 0 ? '00' : '30'
    }`,
);

/** What a user who first chooses to set hours starts from, before changing either end. */
export const FIRST_START = '09:00';
export const FIRST_END = '17:00';

/** Minutes past midnight of an "HH:MM" time, which is the only shape the API accepts. */
export function minutesOf(time: string): number {
  return Number(time.slice(0, 2)) * 60 + Number(time.slice(3, 5));
}

/** How many minutes of a day the window covers, running past midnight if it has to. */
export function coveredMinutes(window: TimeWindow): number {
  const span = minutesOf(window.end) - minutesOf(window.start);
  return span > 0 ? span : span + MINUTES_IN_A_DAY;
}

/** The window as parts of a day: where it starts on the ring, and how much of the ring it takes. */
export function onTheRing(window: TimeWindow): {
  start: number;
  fraction: number;
} {
  return {
    start: minutesOf(window.start) / MINUTES_IN_A_DAY,
    fraction: coveredMinutes(window) / MINUTES_IN_A_DAY,
  };
}

/** Hours covered, to the half hour: the number, and how the figure in the ring reads it. */
export function hoursFigure(window: TimeWindow): {
  count: number;
  hours: string;
} {
  const count = coveredMinutes(window) / 60;
  return {
    count,
    hours: Number.isInteger(count) ? String(count) : count.toFixed(1),
  };
}

/**
 * The timezone the phone is in, which is what a time somebody picks on it means.
 *
 * Read when the window is made rather than stored separately, so a window always travels with the
 * zone it was chosen in.
 */
export function deviceZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
}

export function firstWindow(zone: string = deviceZone()): TimeWindow {
  return { start: FIRST_START, end: FIRST_END, zone };
}

/**
 * The window with one end moved, or null when that would leave it covering nothing.
 *
 * The server refuses a window that starts where it ends, so the chooser never offers one.
 */
export function withEnd(
  window: TimeWindow,
  end: 'start' | 'end',
  time: string,
): TimeWindow | null {
  const next = { ...window, [end]: time };
  return next.start === next.end ? null : next;
}
