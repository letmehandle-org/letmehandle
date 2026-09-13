/**
 * How long to wait, in the fewest words: "24 seconds", "3 minutes", "2 hours".
 *
 * Rounded up, so somebody told three minutes is never refused again at two and a half.
 */
import type { TFunction } from 'i18next';

import { minutesAndSeconds } from '../time/clock';

export function waitWords(seconds: number, t: TFunction): string {
  if (seconds < 60) {
    return t('wait.seconds', { count: Math.max(1, Math.ceil(seconds)) });
  }
  if (seconds < 3600) {
    return t('wait.minutes', { count: Math.ceil(seconds / 60) });
  }
  return t('wait.hours', { count: Math.ceil(seconds / 3600) });
}

/** "0:24", for a countdown on a button. */
export function clockWords(seconds: number): string {
  return minutesAndSeconds(Math.ceil(seconds));
}
