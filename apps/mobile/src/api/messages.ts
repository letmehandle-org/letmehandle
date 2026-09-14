/** Turns a failure into the message a screen shows. */
import { ApiError, NetworkError } from './errors';

export type Translate = (key: string) => string;

export interface Messages {
  /** Shown when the request was refused for a reason the user can do something about. */
  readonly refused: string;
  /** Shown when there have been too many attempts. Omitted where no limit applies. */
  readonly rateLimited?: string;
}

/** Unreachable first, then a refusal the user can act on, else a general message. */
export function describeFailure(
  error: unknown,
  t: Translate,
  messages: Messages,
): string {
  if (error instanceof NetworkError) {
    return t('common.noConnection');
  }

  if (error instanceof ApiError) {
    if (error.isRateLimited && messages.rateLimited !== undefined) {
      return messages.rateLimited;
    }
    // Only a 422 or a sign-in refusal is the user's to fix.
    if (error.status === 422 || error.needsSignIn) {
      return messages.refused;
    }
  }

  return t('common.somethingWentWrong');
}
