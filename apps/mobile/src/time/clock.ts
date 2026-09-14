/** Clock arithmetic the screens share: minutes and seconds, and local midnight. */

/** "1:28" for a whole number of seconds; nothing below zero. */
export function minutesAndSeconds(seconds: number): string {
  const whole = Math.max(0, seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
}

/** Midnight at the start of the day an instant falls on, in the phone's own zone. */
export function localMidnight(instant: Date): Date {
  return new Date(instant.getFullYear(), instant.getMonth(), instant.getDate());
}
