/**
 * Where the session lives on the device.
 *
 * The Keychain on iOS and the Keystore on Android, not async storage: async storage is a plain
 * file, readable by anything that gets at the device's filesystem, and what is being stored
 * here is a credential that renews itself indefinitely.
 *
 * Every read is allowed to fail and return nothing. A device restored from a backup, a
 * biometric prompt declined, a keychain item invalidated by a passcode change — all of these
 * are ordinary, and all of them mean the same thing: there is no session, so sign in again.
 */
import * as Keychain from 'react-native-keychain';

import type { TokenPair } from '@letmehandle/api-client';

const SERVICE = 'org.letmehandle.app.session';

export interface StoredSession {
  readonly accessToken: string;
  readonly refreshToken: string;
  /** Epoch milliseconds. When the access token stops being accepted. */
  readonly accessTokenExpiresAt: number;
}

/**
 * Renew slightly before expiry rather than after.
 *
 * A token that expires while a request is in flight produces a failure the user sees. Thirty
 * seconds is longer than any request this app makes and short enough not to waste most of the
 * token's life.
 */
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
    // Available after the first unlock, so that a notification arriving while the phone is
    // locked can still be acted on when the user picks it up — and never restored onto a
    // different device from a backup.
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
    // A keychain read can fail for reasons that are not this app's business: a declined
    // prompt, an item invalidated by a passcode change, a restored backup. Every one of them
    // means the same thing to the caller.
    return null;
  }
}

export async function clearSession(): Promise<void> {
  try {
    await Keychain.resetGenericPassword({ service: SERVICE });
  } catch {
    // Nothing to do and nothing to tell the user: the session is gone from this app's point of
    // view either way, and the alternative is refusing to sign somebody out.
  }
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
    // Stored by an older version of the app, or corrupted. Treated as no session rather than
    // as a crash on launch, which is what a thrown parse error here would be.
    return null;
  }
}
