/** The session in the Keychain or Keystore; any read failure means there is no session. */
import * as Keychain from 'react-native-keychain';

import type { TokenPair } from '@letmehandle/api-client';

const SERVICE = 'org.letmehandle.app.session';

export interface StoredSession {
  readonly accessToken: string;
  readonly refreshToken: string;
  /** Epoch milliseconds at which the access token stops being accepted. */
  readonly accessTokenExpiresAt: number;
}

/** How long before expiry a token is renewed. */
const RENEW_BEFORE_EXPIRY_MS = 30_000;

export function sessionFromTokens(
  tokens: TokenPair,
  now: number = Date.now(),
): StoredSession {
  return {
    accessToken: tokens.access_token,
    refreshToken: tokens.refresh_token,
    accessTokenExpiresAt: now + tokens.expires_in_seconds * 1000,
  };
}

export function needsRenewal(
  session: StoredSession,
  now: number = Date.now(),
): boolean {
  return session.accessTokenExpiresAt - RENEW_BEFORE_EXPIRY_MS <= now;
}

export async function saveSession(session: StoredSession): Promise<void> {
  await Keychain.setGenericPassword('session', JSON.stringify(session), {
    service: SERVICE,
    // Readable after first unlock, and never restored to another device.
    accessible: Keychain.ACCESSIBLE.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY,
  });
}

export async function loadSession(): Promise<StoredSession | null> {
  try {
    const stored = await Keychain.getGenericPassword({ service: SERVICE });
    if (stored === false) {
      return null;
    }
    return parseSession(stored.password);
  } catch {
    // A declined prompt, an invalidated item or a restored backup all read as no session.
    return null;
  }
}

export async function clearSession(): Promise<void> {
  try {
    await Keychain.resetGenericPassword({ service: SERVICE });
  } catch {}
}

function parseSession(raw: string): StoredSession | null {
  try {
    const parsed: unknown = JSON.parse(raw);
    if (
      typeof parsed !== 'object' ||
      parsed === null ||
      typeof (parsed as StoredSession).accessToken !== 'string' ||
      typeof (parsed as StoredSession).refreshToken !== 'string' ||
      typeof (parsed as StoredSession).accessTokenExpiresAt !== 'number'
    ) {
      return null;
    }
    return parsed as StoredSession;
  } catch {
    // Unreadable or malformed contents read as no session.
    return null;
  }
}
