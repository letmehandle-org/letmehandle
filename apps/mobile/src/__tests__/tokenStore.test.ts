/** Where the session lives, and every way reading it comes back empty. */
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
    // A declined prompt, an invalidated item or a restored backup all read as no session.
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
    // A malformed stored value reads as no session.
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
    keychain.resetGenericPassword.mockRejectedValueOnce(new Error('no'));
    await expect(clearSession()).resolves.toBeUndefined();
  });
});
