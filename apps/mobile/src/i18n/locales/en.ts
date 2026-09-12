/**
 * British English, the only shipped locale (D-017).
 *
 * Keys are grouped by the screen or concept they belong to. A key that is used in two places
 * belongs under `common`; duplicating a string under two keys means one of them will be
 * changed and the other will not.
 */
export const en = {
  common: {
    appName: 'LetMeHandle',
  },
  home: {
    title: 'LetMeHandle',
    subtitle: 'Nothing is handling your calls yet.',
  },
} as const;

export type Translations = typeof en;
