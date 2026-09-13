/**
 * What went wrong, in terms a screen can act on.
 */
import { ERROR_CODES, type ApiErrorBody } from '@letmehandle/api-client';

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly correlationId: string | undefined;
  /** How long the server said to wait, from `Retry-After`, when it said. */
  readonly retryAfterSeconds: number | undefined;

  constructor(status: number, body: ApiErrorBody, retryAfterSeconds?: number) {
    super(body.message);
    this.name = 'ApiError';
    this.status = status;
    this.code = body.error;
    this.correlationId = body.correlation_id;
    this.retryAfterSeconds = retryAfterSeconds;
  }

  /** Whether signing in again is the answer. */
  get needsSignIn(): boolean {
    return (
      this.code === ERROR_CODES.notAuthenticated ||
      this.code === ERROR_CODES.invalidCredentials
    );
  }

  get isRateLimited(): boolean {
    return this.code === ERROR_CODES.rateLimited;
  }
}

/**
 * The network did not answer.
 *
 * Distinct from an `ApiError`, which means the server answered and said no. A screen shows
 * these differently: one is "try again", the other is "that code is wrong".
 */
export class NetworkError extends Error {
  constructor(cause: unknown) {
    super('The service could not be reached.');
    this.name = 'NetworkError';
    this.cause = cause;
  }
}
