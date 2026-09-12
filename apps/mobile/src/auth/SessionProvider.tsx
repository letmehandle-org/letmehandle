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

import type { Profile } from '@letmehandle/api-client';

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

export interface SessionContextValue {
  readonly status: SessionStatus;
  readonly profile: Profile | null;
  readonly api: ApiClient;
  requestCode(phoneNumber: string): Promise<string>;
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
          const renewed = sessionFromTokens(tokens);
          session.current = renewed;
          await saveSession(renewed);
          return renewed.accessToken;
        } catch {
          // Renewal failing means the session is over — expired, revoked, or detected as
          // replayed. All of them mean the same thing to somebody holding a phone.
          return null;
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
      // not open on an error it could have avoided.
      if (needsRenewal(stored) && (await handle.renew()) === null) {
        if (!cancelled) {
          await forget();
        }
        return;
      }

      try {
        const current = await client.me();
        if (!cancelled) {
          setProfile(current);
          setStatus('signed-in');
        }
      } catch {
        if (!cancelled) {
          await forget();
        }
      }
    };

    restore().catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [client, forget, handle]);

  const requestCode = useCallback(
    async (phoneNumber: string): Promise<string> => {
      const issued = await client.requestChallenge(phoneNumber);
      return issued.challenge_id;
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
    // Told to the backend first, so the refresh token is revoked there rather than only
    // forgotten here. A failure does not stop the local sign-out: a user who asked to be
    // signed out is signed out.
    if (current !== null) {
      await client.signOut(current.refreshToken).catch(() => undefined);
    }
    await forget();
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
