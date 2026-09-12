/**
 * The backend's wire types, named.
 *
 * `schema.ts` is generated and unreadable by design. These aliases are the readable surface:
 * they name the handful of shapes the app actually uses, so a screen imports `TokenPair`
 * rather than reaching into a generated path that changes shape whenever the generator does.
 */
import type { components, paths } from './schema';

export type { components, paths };

type Schemas = components['schemas'];

export type ChallengeRequest = Schemas['ChallengeRequest'];
export type ChallengeResponse = Schemas['ChallengeResponse'];
export type VerifyRequest = Schemas['VerifyRequest'];
export type RefreshRequest = Schemas['RefreshRequest'];
export type SignOutRequest = Schemas['SignOutRequest'];
export type TokenPair = Schemas['TokenResponse'];
export type Profile = Schemas['ProfileResponse'];
export type UpdateProfileRequest = Schemas['UpdateProfileRequest'];

/**
 * The machine-readable codes the API returns with a failure.
 *
 * A client branches on these. The message beside them is for a person and will be rewritten,
 * so anything that parses one has turned prose into an interface.
 */
export const ERROR_CODES = {
  invalidRequest: 'invalid_request',
  invalidCredentials: 'invalid_credentials',
  notAuthenticated: 'not_authenticated',
  rateLimited: 'rate_limited',
  databaseUnavailable: 'database_unavailable',
  internal: 'internal_error',
} as const;

export type ErrorCode = (typeof ERROR_CODES)[keyof typeof ERROR_CODES];

/** The one error shape every endpoint returns. */
export interface ApiErrorBody {
  error: ErrorCode | string;
  message: string;
  correlation_id?: string;
  detail?: { field: string; problem: string }[];
}
