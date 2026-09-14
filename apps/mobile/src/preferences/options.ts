/** Every choice the backend accepts, in the order it is offered. */
import type { IconName } from '../components/icon/Icon';
import type { Capability, Formality, Verbosity } from '@letmehandle/api-client';

export const CAPABILITIES = [
  'answer_questions_about_availability',
  'share_delivery_instructions',
  'confirm_appointments',
  'reschedule_appointments',
  'decline_on_the_users_behalf',
  'take_a_message',
  'share_contact_details',
] as const satisfies readonly Capability[];

/** Each capability's picture, so the list is scanned by icon before it is read. */
export const CAPABILITY_ICONS: Record<(typeof CAPABILITIES)[number], IconName> =
  {
    answer_questions_about_availability: 'clock',
    share_delivery_instructions: 'truck',
    confirm_appointments: 'cal',
    reschedule_appointments: 'refresh',
    decline_on_the_users_behalf: 'x',
    take_a_message: 'msg',
    share_contact_details: 'card',
  };

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
