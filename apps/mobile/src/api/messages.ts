/**
 * Turning a failure into something worth showing somebody.
 *
 * One place, because the same three questions are asked on every screen that talks to the
 * backend — could we reach it, did it refuse us, or is this something we cannot explain — and
 * answering them separately on each screen is how two screens end up disagreeing about what a
 * 429 means.
 */
import { ApiError, NetworkError } from './errors';

export type Translate = (key: string) => string;

export interface Messages {
  /** Shown when the request was refused for a reason the user can do something about. */
  readonly refused: string;
  /** Shown when there have been too many attempts. Omitted where no limit applies. */
  readonly rateLimited?: string;
}

/**
 * Which message this failure deserves.
 *
 * The order matters. Unreachable is checked first because it is not the server's answer at
 * all; a refusal second, because it is the one the user can act on; and anything else falls
 * through to a general message rather than being guessed at, since a failure nobody
 * anticipated is exactly the kind to be wrong about.
 */
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
    // A refusal the user can act on: their number, or their code. Anything else — a failure
    // inside the service — is not theirs to fix and not theirs to be told about.
    if (error.status === 422 || error.needsSignIn) {
      return messages.refused;
    }
  }

  return t('common.somethingWentWrong');
}
