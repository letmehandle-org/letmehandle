/** The one client for the backend: adds the token, renews once on a 401 and shares one renewal. */
import type {
  CallReportBatch,
  CallReportReceipt,
  ChallengeResponse,
  Onboarding,
  OnboardingStep,
  Preferences,
  PreferencesUpdate,
  Profile,
  TokenPair,
  UpdateProfileRequest,
  CallDetail,
  CallOutcome,
  CallPage,
  Escalation,
  Transcript,
} from '@letmehandle/api-client';

import { environment } from '../config/environment';
import { ApiError, NetworkError } from './errors';
import type { VoiceCatalogue, VoiceSelection } from './voice';

/** Which calls to list. Each filter is the API's own. */
export interface CallQuery {
  readonly outcome?: CallOutcome;
  readonly humanJoined?: boolean;
  readonly cursor?: string | null;
  /** Only calls that started at or after this instant, in ISO 8601 with its zone. */
  readonly from?: string;
  /** How many a page holds, from 1 to 100. */
  readonly limit?: number;
}

/** How long a request may take before it is treated as the network not answering. */
export const REQUEST_TIMEOUT_MS = 15_000;

export interface SessionHandle {
  /** The access token to send, or nothing when signed out. */
  accessToken(): string | null;
  /** The new access token, null when the server refused, or a throw when it could not be reached. */
  renew(): Promise<string | null>;
  /** Called when renewal fails and the session is over. */
  onSignedOut(): void;
}

interface RequestOptions {
  readonly method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  readonly path: string;
  readonly body?: unknown;
  readonly authenticated?: boolean;
}

export class ApiClient {
  private readonly baseUrl: string;
  private readonly session: SessionHandle;
  /** The renewal in flight, shared by everything that needs one. */
  private renewal: Promise<string | null> | null = null;

  private readonly timeoutMs: number;

  constructor(
    session: SessionHandle,
    baseUrl: string = environment.apiBaseUrl,
    timeoutMs: number = REQUEST_TIMEOUT_MS,
  ) {
    this.baseUrl = baseUrl;
    this.session = session;
    this.timeoutMs = timeoutMs;
  }

  // ------------------------------------------------------------- signing in

  requestChallenge(phoneNumber: string): Promise<ChallengeResponse> {
    return this.send({
      method: 'POST',
      path: '/v1/auth/challenge',
      body: { phone_number: phoneNumber },
    });
  }

  verify(challengeId: string, code: string): Promise<TokenPair> {
    return this.send({
      method: 'POST',
      path: '/v1/auth/verify',
      body: { challenge_id: challengeId, code },
    });
  }

  refresh(refreshToken: string): Promise<TokenPair> {
    return this.send({
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
    return this.authorised('GET', '/v1/me');
  }

  updateMe(changes: UpdateProfileRequest): Promise<Profile> {
    return this.authorised('PATCH', '/v1/me', changes);
  }

  /** The account and everything held because of it, now. Live calls are ended first. */
  deleteAccount(): Promise<void> {
    return this.authorised('DELETE', '/v1/me');
  }

  // ----------------------------------------------------------- preferences

  preferences(): Promise<Preferences> {
    return this.authorised('GET', '/v1/preferences');
  }

  /** Change some of it; PATCH, because PUT would reset every section not mentioned. */
  updatePreferences(changes: PreferencesUpdate): Promise<Preferences> {
    return this.authorised('PATCH', '/v1/preferences', changes);
  }

  // ----------------------------------------------------------------- voice

  /** The voices on offer and what the deployment's provider can do with them (D-009). */
  voices(): Promise<VoiceCatalogue> {
    return this.authorised('GET', '/v1/voices');
  }

  voiceSelection(): Promise<VoiceSelection> {
    return this.authorised('GET', '/v1/preferences/voice');
  }

  /** Choose a voice, or hand the choice back to the provider with an explicit null. */
  chooseVoice(voiceId: string | null): Promise<VoiceSelection> {
    return this.authorised('PUT', '/v1/preferences/voice', {
      persona_voice_id: voiceId,
    });
  }

  // ------------------------------------------------------------ onboarding

  onboarding(): Promise<Onboarding> {
    return this.authorised('GET', '/v1/onboarding');
  }

  /** Record a step as answered, or deliberately passed over. */
  recordOnboardingStep(
    step: OnboardingStep,
    skipped: boolean,
  ): Promise<Onboarding> {
    return this.authorised('POST', '/v1/onboarding', { step, skipped });
  }

  // ----------------------------------------------------------------- calls

  /** What this handset observed about its own calls; accepted and duplicate ids may be forgotten. */
  reportCalls(batch: CallReportBatch): Promise<CallReportReceipt> {
    return this.authorised('POST', '/v1/calls/reports', batch);
  }

  /** One page of call history, newest first; the cursor is null when nothing is older. */
  calls(query: CallQuery = {}): Promise<CallPage> {
    const params = new URLSearchParams();
    if (query.outcome !== undefined) {
      params.set('outcome', query.outcome);
    }
    if (query.humanJoined !== undefined) {
      params.set('human_joined', String(query.humanJoined));
    }
    if (query.from !== undefined) {
      params.set('from', query.from);
    }
    if (query.limit !== undefined) {
      params.set('limit', String(query.limit));
    }
    if (query.cursor != null) {
      params.set('cursor', query.cursor);
    }
    const search = params.toString();
    return this.authorised(
      'GET',
      search === '' ? '/v1/calls' : `/v1/calls?${search}`,
    );
  }

  call(callId: string): Promise<CallDetail> {
    return this.authorised('GET', `/v1/calls/${encodeURIComponent(callId)}`);
  }

  /** What was said. `404 transcript_not_recorded` and `410 transcript_purged` are answers, not faults. */
  transcript(callId: string): Promise<Transcript> {
    return this.authorised(
      'GET',
      `/v1/calls/${encodeURIComponent(callId)}/transcript`,
    );
  }

  /** The summary and the words, gone; succeeds whether or not there was anything to delete. */
  deleteCall(callId: string): Promise<void> {
    return this.authorised('DELETE', `/v1/calls/${encodeURIComponent(callId)}`);
  }

  /** Why the assistant wanted the user on a call, in the words the notification carried. */
  escalation(callId: string): Promise<Escalation> {
    return this.authorised(
      'GET',
      `/v1/escalations/${encodeURIComponent(callId)}`,
    );
  }

  // ---------------------------------------------------------------- sending

  private authorised<T>(
    method: RequestOptions['method'],
    path: string,
    body?: unknown,
  ): Promise<T> {
    return this.send<T>({ method, path, body, authenticated: true });
  }

  private async send<T>(options: RequestOptions): Promise<T> {
    const sent = this.session.accessToken();
    const first = await this.attempt(options, sent);

    if (first.status !== 401 || options.authenticated !== true) {
      return this.unwrap<T>(first);
    }

    // A token renewed while this request was out is reused rather than renewed again.
    const current = this.session.accessToken();
    const renewed =
      current !== null && current !== sent ? current : await this.renewOnce();
    if (renewed === null) {
      this.session.onSignedOut();
      return this.unwrap<T>(first);
    }

    // One retry only; a second 401 is returned as it is.
    return this.unwrap<T>(await this.attempt(options, renewed));
  }

  private async renewOnce(): Promise<string | null> {
    // Requests arriving mid-renewal wait for it, since a second rotation reads as a replayed token.
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

    // Aborts a request the network swallows instead of refusing.
    const abort = new AbortController();
    const timer = setTimeout(() => {
      abort.abort();
    }, this.timeoutMs);
    try {
      return await fetch(`${this.baseUrl}${options.path}`, {
        method: options.method,
        headers,
        body:
          options.body === undefined ? undefined : JSON.stringify(options.body),
        signal: abort.signal,
      });
    } catch (cause) {
      throw new NetworkError(cause);
    } finally {
      clearTimeout(timer);
    }
  }

  private async unwrap<T>(response: Response): Promise<T> {
    if (response.status === 204) {
      return null as T;
    }

    const payload: unknown = await response.json().catch(() => null);

    if (!response.ok) {
      const retryAfter = Number(response.headers.get('Retry-After'));
      throw new ApiError(
        response.status,
        {
          error: readString(payload, 'error') ?? 'internal_error',
          message: readString(payload, 'message') ?? 'Something went wrong.',
          correlation_id: readString(payload, 'correlation_id'),
        },
        Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : undefined,
      );
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
