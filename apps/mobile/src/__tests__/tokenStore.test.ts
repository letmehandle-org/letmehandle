/**
 * Where the session lives, and every way reading it can come back empty.
 */
import * as Keychain from 'react-native-keychain';

import {
  clearSession,
  loadSession,
  needsRenewal,
  saveSession,
  sessionFromTokens,
  type StoredSession,
} from '../auth/tokenStore';

const NOW = 1_800_000_000_000;

const A_SESSION: StoredSession = {
  accessToken: 'an-access-token',
  refreshToken: 'a-refresh-token',
  accessTokenExpiresAt: NOW + 900_000,
};

const keychain = Keychain as jest.Mocked<typeof Keychain>;

/** What the library returns for a stored item. The storage kind is irrelevant here. */
function stored(password: string): Keychain.UserCredentials {
  return {
    service: 'org.letmehandle.app.session',
    username: 'session',
    password,
    storage: Keychain.STORAGE_TYPE.AES_GCM,
  };
}

beforeEach(() => {
  jest.clearAllMocks();
});

describe('building a session from tokens', () => {
  it('turns a lifetime into a moment', () => {
    const session = sessionFromTokens(
      {
        access_token: 'a',
        refresh_token: 'r',
        token_type: 'Bearer',
        expires_in_seconds: 900,
      },
      NOW,
    );
    expect(session.accessTokenExpiresAt).toBe(NOW + 900_000);
  });
});

describe('deciding when to renew', () => {
  it('does not renew a fresh token', () => {
    expect(needsRenewal(A_SESSION, NOW)).toBe(false);
  });

  it('renews shortly before expiry rather than after it', () => {
    // A token that expires while a request is in flight produces a failure the user sees.
    expect(
      needsRenewal(A_SESSION, A_SESSION.accessTokenExpiresAt - 29_000),
    ).toBe(true);
  });

  it('renews an expired token', () => {
    expect(needsRenewal(A_SESSION, A_SESSION.accessTokenExpiresAt + 1)).toBe(
      true,
    );
  });
});

describe('storing a session', () => {
  it('writes to the keychain, tied to this device', async () => {
    await saveSession(A_SESSION);

    expect(keychain.setGenericPassword).toHaveBeenCalledWith(
      'session',
      JSON.stringify(A_SESSION),
      expect.objectContaining({
        // Never restored onto a different device from a backup: a session is not a setting.
        accessible: Keychain.ACCESSIBLE.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY,
      }),
    );
  });

  it('reads back what it wrote', async () => {
    keychain.getGenericPassword.mockResolvedValueOnce(
      stored(JSON.stringify(A_SESSION)),
    );

    expect(await loadSession()).toEqual(A_SESSION);
  });
});

describe('reading a session that is not there', () => {
  it('returns nothing when the keychain is empty', async () => {
    keychain.getGenericPassword.mockResolvedValueOnce(false);
    expect(await loadSession()).toBeNull();
  });

  it('returns nothing when the keychain throws', async () => {
    // A declined prompt, an item invalidated by a passcode change, a restored backup. All of
    // them mean the same thing to the caller: sign in again.
    keychain.getGenericPassword.mockRejectedValueOnce(
      new Error('user cancelled'),
    );
    expect(await loadSession()).toBeNull();
  });

  it.each([
    ['not json at all'],
    ['{}'],
    ['{"accessToken": "a"}'],
    ['{"accessToken": "a", "refreshToken": "r"}'],
    ['{"accessToken": 1, "refreshToken": "r", "accessTokenExpiresAt": 1}'],
    ['null'],
  ])('returns nothing for stored value %p', async raw => {
    // Written by an older version, or corrupted. Treated as no session rather than as a crash
    // on launch, which is what a thrown parse error here would be.
    keychain.getGenericPassword.mockResolvedValueOnce(stored(raw));

    expect(await loadSession()).toBeNull();
  });
});

describe('clearing a session', () => {
  it('resets the keychain entry', async () => {
    await clearSession();
    expect(keychain.resetGenericPassword).toHaveBeenCalled();
  });

  it('does not fail when the keychain does', async () => {
    // The alternative is refusing to sign somebody out, which is worse than a stale entry.
    keychain.resetGenericPassword.mockRejectedValueOnce(new Error('no'));
    await expect(clearSession()).resolves.toBeUndefined();
  });
});
