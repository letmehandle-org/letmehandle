/** What the preference calls put on the wire, PATCH for partial changes. */
import { ApiClient, type SessionHandle } from '../api/client';
import { DEFAULT_PREFERENCES, ONBOARDING_COMPLETE } from './support/backend';
import { jsonResponse } from './support/http';

const BASE = 'http://localhost:8000';

const handle: SessionHandle = {
  accessToken: () => 'a-token',
  renew: async () => null,
  onSignedOut: () => undefined,
};

interface Call {
  readonly url: string;
  readonly init: RequestInit;
}

function fetching(body: unknown): { calls: Call[]; client: ApiClient } {
  const calls: Call[] = [];
  globalThis.fetch = (async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return jsonResponse(200, body);
  }) as unknown as typeof fetch;

  return { calls, client: new ApiClient(handle, BASE) };
}

describe('reading preferences', () => {
  it('asks for them as the signed-in user', async () => {
    const { calls, client } = fetching(DEFAULT_PREFERENCES);

    await expect(client.preferences()).resolves.toEqual(DEFAULT_PREFERENCES);
    expect(calls[0].url).toBe(`${BASE}/v1/preferences`);
    expect(calls[0].init.method).toBe('GET');
    expect(
      (calls[0].init.headers as Record<string, string>).Authorization,
    ).toBe('Bearer a-token');
  });
});

describe('changing preferences', () => {
  it('patches, so that a section it did not mention is left alone', async () => {
    const { calls, client } = fetching(DEFAULT_PREFERENCES);

    await client.updatePreferences({ authority: { capabilities: [] } });

    expect(calls[0].init.method).toBe('PATCH');
    expect(JSON.parse(String(calls[0].init.body))).toEqual({
      authority: { capabilities: [] },
    });
  });
});

describe('onboarding', () => {
  it('reads where setup is', async () => {
    const { calls, client } = fetching(ONBOARDING_COMPLETE);

    await expect(client.onboarding()).resolves.toEqual(ONBOARDING_COMPLETE);
    expect(calls[0].url).toBe(`${BASE}/v1/onboarding`);
    expect(calls[0].init.method).toBe('GET');
  });

  it('says whether a step was answered or passed over', async () => {
    const { calls, client } = fetching(ONBOARDING_COMPLETE);

    await client.recordOnboardingStep('hours', true);

    expect(calls[0].init.method).toBe('POST');
    expect(JSON.parse(String(calls[0].init.body))).toEqual({
      step: 'hours',
      skipped: true,
    });
  });
});
