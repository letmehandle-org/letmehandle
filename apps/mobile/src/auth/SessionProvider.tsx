/**
 * Who is signed in, for the whole application.
 *
 * One place holds the session, so that a screen asks whether somebody is signed in rather than
 * reading a token and deciding for itself. The navigator reads `status` to choose between the
 * sign-in screens and the application; nothing else needs to know a token exists.
 */
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { AppState, type AppStateStatus } from 'react-native';

import type { Profile } from '@letmehandle/api-client';

import { ApiError } from '../api/errors';
import { ApiClient, type SessionHandle } from '../api/client';
import {
  clearSession,
  loadSession,
  needsRenewal,
  saveSession,
  sessionFromTokens,
  type StoredSession,
} from './tokenStore';

export type SessionStatus = 'restoring' | 'signed-out' | 'signed-in';

/** A code on its way: which challenge to answer, and when another may be asked for. */
export interface CodeSent {
  readonly challengeId: string;
  readonly resendAfterSeconds: number;
}

/**
 * Whether a failure means the session is over rather than out of reach.
 *
 * Only a 401 from the server. Everything else — no network, a timeout, a 5xx, a rate limit — is
 * something that will pass, and ending a session over it is how somebody who opened the app on a
 * train gets asked for their number again.
 */
export function endsSession(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

export interface SessionContextValue {
  readonly status: SessionStatus;
  readonly profile: Profile | null;
  readonly api: ApiClient;
  requestCode(phoneNumber: string): Promise<CodeSent>;
  signIn(challengeId: string, code: string): Promise<void>;
  signOut(): Promise<void>;
  refreshProfile(): Promise<void>;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function useSession(): SessionContextValue {
  const value = useContext(SessionContext);
  if (value === null) {
    // A screen rendered outside the provider would otherwise read undefined and fail somewhere
    // unrelated. This says where the mistake is.
    throw new Error('useSession must be used inside a SessionProvider');
  }
  return value;
}

export function SessionProvider({
  children,
}: {
  children: React.ReactNode;
}): React.JSX.Element {
  const [status, setStatus] = useState<SessionStatus>('restoring');
  const [profile, setProfile] = useState<Profile | null>(null);

  // A ref, not state: the client reads it during a request, and a stale closure over a state
  // value would send the token that was current when the screen last rendered.
  const session = useRef<StoredSession | null>(null);

  const forget = useCallback(async (): Promise<void> => {
    session.current = null;
    setProfile(null);
    setStatus('signed-out');
    await clearSession();
  }, []);

  // The client and the session handle need each other: the client asks the handle to renew,
  // and renewing means calling the client. Held through a ref rather than left to closure
  // timing, so the cycle is visible instead of being something that happens to work.
  const clientRef = useRef<ApiClient | null>(null);

  const handle = useMemo<SessionHandle>(
    () => ({
      accessToken: () => session.current?.accessToken ?? null,
      renew: async () => {
        const current = session.current;
        const api = clientRef.current;
        if (current === null || api === null) {
          return null;
        }
        try {
          const tokens = await api.refresh(current.refreshToken);
          if (session.current !== current) {
            // Signed out, or signed in again, while renewing: the renewal belongs to nobody.
            return session.current?.accessToken ?? null;
          }
          const renewed = sessionFromTokens(tokens);
          session.current = renewed;
          await saveSession(renewed);
          return renewed.accessToken;
        } catch (error) {
          // Only the server saying no ends a session: expired, revoked, or detected as replayed.
          // A phone with no signal, a server that is restarting, a request that timed out — none
          // of those is a reason to ask somebody for their number again, so they are raised to
          // the request that needed the renewal and the session is kept for the next one.
          if (endsSession(error)) {
            return null;
          }
          throw error;
        }
      },
      onSignedOut: () => {
        // Not awaited: this is called from inside a failed request, which has its own error to
        // return. The catch is there so that a failure to clear storage cannot surface as an
        // unhandled rejection somewhere unrelated.
        forget().catch(() => undefined);
      },
    }),
    [forget],
  );

  // Built once. A new client per render would lose the renewal in flight, which is the whole
  // reason concurrent requests share one.
  const client = useMemo(() => {
    const built = new ApiClient(handle);
    clientRef.current = built;
    return built;
  }, [handle]);

  const refreshProfile = useCallback(async (): Promise<void> => {
    setProfile(await client.me());
  }, [client]);

  useEffect(() => {
    let cancelled = false;

    const restore = async (): Promise<void> => {
      const stored = await loadSession();
      if (stored === null) {
        if (!cancelled) {
          setStatus('signed-out');
        }
        return;
      }

      session.current = stored;

      // Renewed before the first request rather than after one fails, so the application does
      // not open on an error it could have avoided. Opening with no signal still opens signed
      // in: the stored session is the user's until the server says otherwise.
      try {
        if (needsRenewal(stored) && (await handle.renew()) === null) {
          if (!cancelled) {
            await forget();
          }
          return;
        }
        const current = await client.me();
        if (!cancelled) {
          setProfile(current);
          setStatus('signed-in');
        }
      } catch (error) {
        if (cancelled) {
          return;
        }
        if (endsSession(error)) {
          await forget();
        } else {
          // Signed in without a profile yet; it is fetched again when the app is next in front.
          setStatus('signed-in');
        }
      }
    };

    restore().catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [client, forget, handle]);

  // A profile the app could not fetch while it had no signal, fetched again once it is in front.
  useEffect(() => {
    if (status !== 'signed-in' || profile !== null) {
      return undefined;
    }
    const retry = (state: AppStateStatus): void => {
      if (state === 'active') {
        client
          .me()
          .then(setProfile)
          .catch(() => undefined);
      }
    };
    const subscription = AppState.addEventListener('change', retry);
    return () => {
      subscription.remove();
    };
  }, [status, profile, client]);

  const requestCode = useCallback(
    async (phoneNumber: string): Promise<CodeSent> => {
      const issued = await client.requestChallenge(phoneNumber);
      return {
        challengeId: issued.challenge_id,
        resendAfterSeconds: issued.resend_after_seconds ?? 0,
      };
    },
    [client],
  );

  const signIn = useCallback(
    async (challengeId: string, code: string): Promise<void> => {
      const tokens = await client.verify(challengeId, code);
      const started = sessionFromTokens(tokens);
      session.current = started;
      await saveSession(started);
      setProfile(await client.me());
      setStatus('signed-in');
    },
    [client],
  );

  const signOut = useCallback(async (): Promise<void> => {
    const current = session.current;
    await forget();
    if (current !== null) {
      await client.signOut(current.refreshToken).catch(() => undefined);
    }
  }, [client, forget]);

  const value = useMemo<SessionContextValue>(
    () => ({
      status,
      profile,
      api: client,
      requestCode,
      signIn,
      signOut,
      refreshProfile,
    }),
    [status, profile, client, requestCode, signIn, signOut, refreshProfile],
  );

  return (
    <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
  );
}
