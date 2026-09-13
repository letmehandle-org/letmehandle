/** Who is signed in, for the whole app; screens read `status` and never a token. */
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

/** Whether a failure ends the session: only a 401 does (D-036). */
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

  // A ref, so a request always reads the current session rather than a render's copy.
  const session = useRef<StoredSession | null>(null);

  const forget = useCallback(async (): Promise<void> => {
    session.current = null;
    setProfile(null);
    setStatus('signed-out');
    await clearSession();
  }, []);

  // The client and the handle need each other, so the handle reaches the client through a ref.
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
          // Only a refusal ends the session; network and server faults reach the request that needed renewal.
          if (endsSession(error)) {
            return null;
          }
          throw error;
        }
      },
      onSignedOut: () => {
        // Not awaited: the failed request returns its own error.
        forget().catch(() => undefined);
      },
    }),
    [forget],
  );

  // One client for the provider's life, so concurrent requests share its renewal.
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

      // Renews an expiring token before the first request; with no signal the app still opens signed in.
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
        resendAfterSeconds: issued.resend_after_seconds,
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
      // A profile that cannot be read yet is fetched again later; the code is already spent.
      const current = await client.me().catch((error: unknown) => {
        if (endsSession(error)) {
          throw error;
        }
        return null;
      });
      setProfile(current);
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
