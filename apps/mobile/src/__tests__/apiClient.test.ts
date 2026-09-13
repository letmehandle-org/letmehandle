/**
 * The client's two rules: retry an unauthorised request once, and renew only once at a time.
 *
 * Both exist because of what the backend does with refresh tokens. Renewing rotates them, and
 * presenting a rotated one is treated as theft — so two concurrent renewals would sign the
 * user out, and an unbounded retry would do it repeatedly.
 */
import { ApiClient, type SessionHandle } from '../api/client';
import { ApiError, NetworkError } from '../api/errors';
import { jsonResponse } from './support/http';

const BASE = 'http://localhost:8000';

interface Reply {
  readonly status: number;
  readonly body?: unknown;
}

class FakeFetch {
  readonly calls: { url: string; init: RequestInit }[] = [];
  private readonly replies: Reply[];

  constructor(replies: Reply[]) {
    this.replies = [...replies];
  }

  get fn(): typeof fetch {
    return (async (url: string, init: RequestInit) => {
      this.calls.push({ url, init });
      const reply = this.replies.shift() ?? { status: 200, body: {} };
      return jsonResponse(reply.status, reply.body ?? {});
    }) as unknown as typeof fetch;
  }

  authorisationHeaders(): (string | undefined)[] {
    return this.calls.map(
      call =>
        (call.init.headers as Record<string, string> | undefined)
          ?.Authorization,
    );
  }
}

function handleFor(options: {
  token?: string | null;
  renewTo?: string | null;
  onRenew?: () => void;
}): SessionHandle & { signedOut: boolean; renewals: number } {
  const state = {
    signedOut: false,
    renewals: 0,
    accessToken: () => options.token ?? null,
    renew: async () => {
      state.renewals += 1;
      options.onRenew?.();
      return options.renewTo ?? null;
    },
    onSignedOut: () => {
      state.signedOut = true;
    },
  };
  return state;
}

describe('unauthenticated requests', () => {
  it('sends no authorisation header', async () => {
    const fake = new FakeFetch([
      {
        status: 202,
        body: {
          challenge_id: 'c',
          expires_in_seconds: 300,
          resend_after_seconds: 0,
        },
      },
    ]);
    globalThis.fetch = fake.fn;

    await new ApiClient(handleFor({ token: 'a-token' }), BASE).requestChallenge(
      '+12025550143',
    );

    expect(fake.authorisationHeaders()).toEqual([undefined]);
  });

  it('does not renew when one fails', async () => {
    // A rejected sign-in code is not an expired session, and renewing here would turn a wrong
    // code into a sign-out.
    const fake = new FakeFetch([
      { status: 401, body: { error: 'invalid_credentials', message: 'no' } },
    ]);
    globalThis.fetch = fake.fn;
    const handle = handleFor({ token: 'a-token', renewTo: 'renewed' });

    await expect(
      new ApiClient(handle, BASE).verify('c', '000000'),
    ).rejects.toBeInstanceOf(ApiError);
    expect(handle.renewals).toBe(0);
  });
});

describe('authenticated requests', () => {
  it('sends the access token', async () => {
    const fake = new FakeFetch([{ status: 200, body: { id: 'u' } }]);
    globalThis.fetch = fake.fn;

    await new ApiClient(handleFor({ token: 'a-token' }), BASE).me();

    expect(fake.authorisationHeaders()).toEqual(['Bearer a-token']);
  });

  it('renews once and retries when the token has expired', async () => {
    const fake = new FakeFetch([
      { status: 401, body: { error: 'not_authenticated', message: 'no' } },
      { status: 200, body: { id: 'u' } },
    ]);
    globalThis.fetch = fake.fn;
    const handle = handleFor({ token: 'stale', renewTo: 'fresh' });

    await new ApiClient(handle, BASE).me();

    expect(handle.renewals).toBe(1);
    expect(fake.authorisationHeaders()).toEqual([
      'Bearer stale',
      'Bearer fresh',
    ]);
  });

  it('retries at most once', async () => {
    // A second failure means the session is genuinely gone. Retrying further turns one expired
    // token into a loop.
    const fake = new FakeFetch([
      { status: 401, body: { error: 'not_authenticated', message: 'no' } },
      { status: 401, body: { error: 'not_authenticated', message: 'no' } },
    ]);
    globalThis.fetch = fake.fn;
    const handle = handleFor({ token: 'stale', renewTo: 'fresh' });

    await expect(new ApiClient(handle, BASE).me()).rejects.toBeInstanceOf(
      ApiError,
    );
    expect(fake.calls).toHaveLength(2);
    expect(handle.renewals).toBe(1);
  });

  it('reports the session as over when renewal fails', async () => {
    const fake = new FakeFetch([
      { status: 401, body: { error: 'not_authenticated', message: 'no' } },
    ]);
    globalThis.fetch = fake.fn;
    const handle = handleFor({ token: 'stale', renewTo: null });

    await expect(new ApiClient(handle, BASE).me()).rejects.toBeInstanceOf(
      ApiError,
    );
    expect(handle.signedOut).toBe(true);
  });

  it('retries with a token renewed meanwhile rather than renewing again', async () => {
    const session = { token: 'stale', renewTo: 'renewed' };
    const handle = handleFor(session);
    const fake = new FakeFetch([
      { status: 401, body: { error: 'not_authenticated', message: 'no' } },
      { status: 200, body: { id: 'u' } },
    ]);
    const send = fake.fn;
    globalThis.fetch = (async (url: string, init: RequestInit) => {
      const response = await send(url, init);
      session.token = 'fresh';
      return response;
    }) as unknown as typeof fetch;

    await new ApiClient(handle, BASE).me();

    expect(handle.renewals).toBe(0);
    expect(fake.authorisationHeaders()).toEqual([
      'Bearer stale',
      'Bearer fresh',
    ]);
  });

  it('renews once for several requests that fail together', async () => {
    // The behaviour a cold start depends on. Renewing rotates the refresh token, so a second
    // renewal would look to the backend exactly like a stolen token being replayed — and
    // would sign the user out for opening the app.
    const fake = new FakeFetch([
      { status: 401, body: { error: 'not_authenticated', message: 'no' } },
      { status: 401, body: { error: 'not_authenticated', message: 'no' } },
      { status: 401, body: { error: 'not_authenticated', message: 'no' } },
      { status: 200, body: { id: 'u' } },
      { status: 200, body: { id: 'u' } },
      { status: 200, body: { id: 'u' } },
    ]);
    globalThis.fetch = fake.fn;
    const handle = handleFor({ token: 'stale', renewTo: 'fresh' });
    const client = new ApiClient(handle, BASE);

    await Promise.all([client.me(), client.me(), client.me()]);

    expect(handle.renewals).toBe(1);
  });
});

describe('failures', () => {
  it('gives up on a request the network swallows, as a network error', async () => {
    let aborted = false;
    globalThis.fetch = ((_url: string, init: RequestInit) =>
      new Promise((_resolve, reject) => {
        init.signal?.addEventListener('abort', () => {
          aborted = true;
          reject(new Error('aborted'));
        });
      })) as unknown as typeof fetch;

    await expect(
      new ApiClient(handleFor({}), BASE, 20).me(),
    ).rejects.toBeInstanceOf(NetworkError);
    expect(aborted).toBe(true);
  });

  it('keeps the session when a renewal cannot reach the server', async () => {
    const fake = new FakeFetch([
      { status: 401, body: { error: 'not_authenticated', message: 'no' } },
    ]);
    globalThis.fetch = fake.fn;
    const handle = {
      ...handleFor({ token: 'stale' }),
      renew: async () => {
        throw new NetworkError(new TypeError('Network request failed'));
      },
    };

    await expect(new ApiClient(handle, BASE).me()).rejects.toBeInstanceOf(
      NetworkError,
    );
    expect(handle.signedOut).toBe(false);
  });

  it('reads how long to wait from a refusal', async () => {
    globalThis.fetch = (async () =>
      jsonResponse(
        429,
        { error: 'rate_limited', message: 'no' },
        { 'Retry-After': '90' },
      )) as unknown as typeof fetch;

    const refused = await new ApiClient(handleFor({}), BASE)
      .requestChallenge('+12025550143')
      .catch((error: unknown) => error);
    expect(refused).toBeInstanceOf(ApiError);
    expect((refused as ApiError).retryAfterSeconds).toBe(90);
    expect((refused as ApiError).isRateLimited).toBe(true);
  });

  it('turns an unreachable service into a network error', async () => {
    globalThis.fetch = (async () => {
      throw new TypeError('Network request failed');
    }) as unknown as typeof fetch;

    await expect(
      new ApiClient(handleFor({}), BASE).me(),
    ).rejects.toBeInstanceOf(NetworkError);
  });

  it('carries the correlation id so a report can be traced', async () => {
    const fake = new FakeFetch([
      {
        status: 500,
        body: {
          error: 'internal_error',
          message: 'no',
          correlation_id: 'abc-123',
        },
      },
    ]);
    globalThis.fetch = fake.fn;

    await expect(
      new ApiClient(handleFor({}), BASE).requestChallenge('+12025550143'),
    ).rejects.toMatchObject({ correlationId: 'abc-123' });
  });

  it('copes with a failure that has no body at all', async () => {
    const fake = new FakeFetch([{ status: 502 }]);
    globalThis.fetch = fake.fn;

    await expect(
      new ApiClient(handleFor({}), BASE).requestChallenge('+12025550143'),
    ).rejects.toBeInstanceOf(ApiError);
  });

  it('treats a 204 as nothing rather than as unparseable', async () => {
    const fake = new FakeFetch([{ status: 204 }]);
    globalThis.fetch = fake.fn;

    await expect(
      new ApiClient(handleFor({}), BASE).signOut('a-refresh-token'),
    ).resolves.toBeUndefined();
  });
});
