/**
 * The one place this app talks to the backend.
 *
 * Screens call methods here. They never see a token, never set a header, and never decide what
 * to do about a 401 — because a rule that lives in every screen is a rule one screen gets
 * wrong.
 *
 * Two behaviours are worth reading before changing anything:
 *
 *   A request that comes back unauthorised is retried once, after renewing the session. The
 *   retry is at most once: a second failure means the session is genuinely gone, and retrying
 *   further would turn one expired token into an infinite loop.
 *
 *   Concurrent requests share a single renewal. A cold start fires several requests at once,
 *   and without this each would renew separately — and because renewing rotates the refresh
 *   token, all but one of those renewals would be treated by the backend as a stolen token and
 *   would sign the user out.
 */
import type {
  ChallengeResponse,
  Profile,
  TokenPair,
  UpdateProfileRequest,
} from '@letmehandle/api-client';

import { environment } from '../config/environment';
import { ApiError, NetworkError } from './errors';

export interface SessionHandle {
  /** The access token to send, or nothing when signed out. */
  accessToken(): string | null;
  /** Renew the session. Returns the new access token, or null when renewal is impossible. */
  renew(): Promise<string | null>;
  /** Called when renewal fails and the session is over. */
  onSignedOut(): void;
}

interface RequestOptions {
  readonly method: 'GET' | 'POST' | 'PATCH';
  readonly path: string;
  readonly body?: unknown;
  readonly authenticated?: boolean;
}

export class ApiClient {
  private readonly baseUrl: string;
  private readonly session: SessionHandle;
  /** The renewal in flight, shared by everything that needs one. */
  private renewal: Promise<string | null> | null = null;

  constructor(
    session: SessionHandle,
    baseUrl: string = environment.apiBaseUrl,
  ) {
    this.baseUrl = baseUrl;
    this.session = session;
  }

  // ------------------------------------------------------------- signing in

  requestChallenge(phoneNumber: string): Promise<ChallengeResponse> {
    return this.send<ChallengeResponse>({
      method: 'POST',
      path: '/v1/auth/challenge',
      body: { phone_number: phoneNumber },
    });
  }

  verify(challengeId: string, code: string): Promise<TokenPair> {
    return this.send<TokenPair>({
      method: 'POST',
      path: '/v1/auth/verify',
      body: { challenge_id: challengeId, code },
    });
  }

  refresh(refreshToken: string): Promise<TokenPair> {
    return this.send<TokenPair>({
      method: 'POST',
      path: '/v1/auth/refresh',
      body: { refresh_token: refreshToken },
    });
  }

  async signOut(refreshToken: string): Promise<void> {
    await this.send<null>({
      method: 'POST',
      path: '/v1/auth/signout',
      body: { refresh_token: refreshToken },
    });
  }

  // --------------------------------------------------------------- profile

  me(): Promise<Profile> {
    return this.send<Profile>({
      method: 'GET',
      path: '/v1/me',
      authenticated: true,
    });
  }

  updateMe(changes: UpdateProfileRequest): Promise<Profile> {
    return this.send<Profile>({
      method: 'PATCH',
      path: '/v1/me',
      body: changes,
      authenticated: true,
    });
  }

  // ---------------------------------------------------------------- sending

  private async send<T>(options: RequestOptions): Promise<T> {
    const first = await this.attempt(options, this.session.accessToken());

    if (first.status !== 401 || options.authenticated !== true) {
      return this.unwrap<T>(first);
    }

    const renewed = await this.renewOnce();
    if (renewed === null) {
      this.session.onSignedOut();
      return this.unwrap<T>(first);
    }

    // Once, and only once. A second failure means the session is genuinely gone.
    return this.unwrap<T>(await this.attempt(options, renewed));
  }

  private async renewOnce(): Promise<string | null> {
    // Everything that arrives while a renewal is in flight waits for that one rather than
    // starting its own. Renewing rotates the refresh token, so two renewals would look to the
    // backend exactly like a stolen token being replayed — and would sign the user out.
    this.renewal ??= this.session.renew().finally(() => {
      this.renewal = null;
    });
    return this.renewal;
  }

  private async attempt(
    options: RequestOptions,
    token: string | null,
  ): Promise<Response> {
    const headers: Record<string, string> = { Accept: 'application/json' };
    if (options.body !== undefined) {
      headers['Content-Type'] = 'application/json';
    }
    if (options.authenticated === true && token !== null) {
      headers.Authorization = `Bearer ${token}`;
    }

    try {
      return await fetch(`${this.baseUrl}${options.path}`, {
        method: options.method,
        headers,
        body:
          options.body === undefined ? undefined : JSON.stringify(options.body),
      });
    } catch (cause) {
      throw new NetworkError(cause);
    }
  }

  private async unwrap<T>(response: Response): Promise<T> {
    if (response.status === 204) {
      return null as T;
    }

    const payload: unknown = await response.json().catch(() => null);

    if (!response.ok) {
      throw new ApiError(response.status, {
        error: readString(payload, 'error') ?? 'internal_error',
        message: readString(payload, 'message') ?? 'Something went wrong.',
        correlation_id: readString(payload, 'correlation_id'),
      });
    }

    return payload as T;
  }
}

function readString(payload: unknown, key: string): string | undefined {
  if (typeof payload !== 'object' || payload === null) {
    return undefined;
  }
  const value = (payload as Record<string, unknown>)[key];
  return typeof value === 'string' ? value : undefined;
}
