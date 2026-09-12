/**
 * British English, the only shipped locale (D-017).
 *
 * Keys are grouped by the screen or concept they belong to. A key used in two places belongs
 * under `common`: duplicating a string under two keys means one of them gets changed and the
 * other does not.
 */
export const en = {
  common: {
    appName: 'LetMeHandle',
    continue: 'Continue',
    back: 'Back',
    tryAgain: 'Try again',
    somethingWentWrong: 'Something went wrong. Please try again.',
    noConnection: 'Could not reach the service. Check your connection.',
  },
  welcome: {
    title: 'LetMeHandle',
    subtitle:
      'An assistant that answers your calls, and knows when to fetch you.',
    start: 'Get started',
  },
  phone: {
    title: 'What is your number?',
    subtitle: 'This is the number your assistant will answer for.',
    label: 'Phone number',
    placeholder: '+12025550143',
    invalid: 'Enter your number in full, including the country code.',
    rateLimited: 'Too many attempts. Try again in a little while.',
  },
  code: {
    title: 'Enter your code',
    subtitle: 'We sent a six-digit code to {{number}}.',
    label: 'Code',
    invalid: 'That code is not valid.',
    resend: 'Send another code',
  },
  profile: {
    title: 'Your profile',
    name: 'Name',
    namePlaceholder: 'What should your assistant call you?',
    number: 'Number',
    save: 'Save',
    saved: 'Saved',
    signOut: 'Sign out',
  },
  home: {
    title: 'LetMeHandle',
    subtitle: 'Nothing is handling your calls yet.',
    profile: 'Profile',
  },
} as const;

export type Translations = typeof en;
