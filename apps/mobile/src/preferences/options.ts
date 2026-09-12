/**
 * Every choice the backend will accept, listed once.
 *
 * The generated types say which values exist but not in what order to offer them, and a screen
 * that writes its own list is a screen that quietly stops offering a value the backend added.
 * Typing each list against the generated union means a value that disappears upstream is a
 * compile error here rather than an option nobody can pick.
 */
import type {
  CallImportance,
  CallerCategory,
  Capability,
  Formality,
  HandlingPosture,
  OnboardingStep,
  Verbosity,
} from '@letmehandle/api-client';

export const POSTURES = [
  'pass_through',
  'handle_with_agent',
  'reject',
] as const satisfies readonly HandlingPosture[];

export const CATEGORIES = [
  'known_contact',
  'delivery',
  'healthcare',
  'education',
  'financial',
  'service_provider',
  'sales',
  'spam',
  'unknown',
] as const satisfies readonly CallerCategory[];

export const CAPABILITIES = [
  'answer_questions_about_availability',
  'share_delivery_instructions',
  'confirm_appointments',
  'reschedule_appointments',
  'decline_on_the_users_behalf',
  'take_a_message',
  'share_contact_details',
] as const satisfies readonly Capability[];

export const FORMALITIES = [
  'warm',
  'neutral',
  'formal',
] as const satisfies readonly Formality[];

export const VERBOSITIES = [
  'brief',
  'normal',
  'detailed',
] as const satisfies readonly Verbosity[];

/**
 * The importance levels, with the name each one is labelled by.
 *
 * The wire value is a number so that "at or above" is a comparison rather than a lookup table.
 * That makes it useless as a translation key, so the name travels beside it.
 */
export const IMPORTANCE_LEVELS = [
  { value: 10, name: 'ignorable' },
  { value: 20, name: 'low' },
  { value: 30, name: 'routine' },
  { value: 40, name: 'notable' },
  { value: 50, name: 'urgent' },
] as const satisfies readonly { value: CallImportance; name: string }[];

/**
 * The steps that edit preferences, which is every step but the introduction.
 *
 * The introduction asks for nothing, so it has nothing to edit afterwards: it exists in the
 * onboarding flow and nowhere else.
 */
export const PREFERENCE_SECTIONS = [
  'call_handling',
  'important_contacts',
  'hours',
  'authority',
  'notifications',
  'personality',
] as const satisfies readonly OnboardingStep[];

export type PreferenceSection = (typeof PREFERENCE_SECTIONS)[number];

/**
 * Whether a step may be passed over.
 *
 * `call_handling` is the exception, and the reason is the product's: there is no safe default
 * for what to do with a call from somebody unknown. The backend refuses to skip it with a 422,
 * so offering the button would mean showing a failure the user could not have avoided.
 */
export function canSkip(step: OnboardingStep): boolean {
  return step !== 'introduction' && step !== 'call_handling';
}
