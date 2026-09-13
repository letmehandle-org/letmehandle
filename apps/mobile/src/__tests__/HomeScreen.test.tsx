/**
 * Home: every state the design draws, each reached only by the facts that make it true.
 */
import {
  act,
  fireEvent,
  render,
  renderHook,
  waitFor,
} from '@testing-library/react-native';
import React from 'react';

import { ApiClient } from '../api/client';
import { SessionProvider } from '../auth/SessionProvider';
import { callScreeningFrom, type CallScreening } from '../calls/callScreening';
import { forgetHome, REFRESH_EVERY_MS, useHome } from '../home/useHome';
import {
  elapsed,
  homeState,
  ringParts,
  startOfToday,
  tally,
} from '../home/today';
import { initialiseI18n } from '../i18n';
import { en } from '../i18n/locales/en';
import { RootNavigator } from '../navigation/RootNavigator';
import { aCall, runningBackend, type HistorySetup } from './support/backend';
import { FakeNativeCallScreening } from './support/nativeCallScreening';
import { fakeOnly } from './support/timers';

jest.mock('../auth/tokenStore', () => ({
  ...jest.requireActual('../auth/tokenStore'),
  loadSession: jest.fn(async () => ({
    accessToken: 'a-token',
    refreshToken: 'a-refresh-token',
    accessTokenExpiresAt: Date.now() + 600_000,
  })),
  saveSession: jest.fn(async () => undefined),
  clearSession: jest.fn(async () => undefined),
}));

const mockSecure = { setSecure: jest.fn() };
jest.mock('../calls/native/NativeSecureScreen', () => ({
  __esModule: true,
  get default() {
    return mockSecure;
  },
}));

type View = Awaited<ReturnType<typeof render>>;

beforeAll(async () => {
  await initialiseI18n('en');
});

beforeEach(() => {
  forgetHome();
  mockSecure.setSecure.mockClear();
});

async function home(
  history: HistorySetup,
  screening: CallScreening | null = null,
  forwarded = false,
): Promise<{ view: View; backend: ReturnType<typeof runningBackend> }> {
  const backend = runningBackend({ startAt: null, history, forwarded });
  const view = await render(
    <SessionProvider>
      <RootNavigator screening={screening} />
    </SessionProvider>,
  );
  await view.findByTestId('home-status');
  return { view, backend };
}

const EARLIER = new Date(Date.now() - 60_000).toISOString();

describe('what Home says', () => {
  it('waits honestly for the first call rather than claiming to be on duty', async () => {
    const { view } = await home({ calls: [] });

    expect(view.getByTestId('home-status')).toHaveAccessibleName(
      en.home.waiting,
    );
    expect(view.getByTestId('home-figure')).toHaveTextContent('0');
    expect(view.getByText(en.home.nothingYet)).toBeOnTheScreen();
    expect(view.getByText(en.home.fillsRing)).toBeOnTheScreen();
    expect(mockSecure.setSecure).not.toHaveBeenCalled();
  });

  it('is on duty with today on the ring and the latest calls, kept out of screenshots', async () => {
    const { view } = await home({
      calls: [
        aCall({ id: 'courier', started_at: EARLIER }),
        aCall({
          id: 'bank',
          outcome: 'handed_to_user',
          human_joined: true,
          started_at: EARLIER,
        }),
        aCall({
          id: 'spam',
          outcome: 'rejected_by_rule',
          caller: {
            category: 'spam',
            display_name: null,
            number_withheld: false,
          },
          started_at: EARLIER,
        }),
        aCall({ id: 'another', started_at: EARLIER }),
      ],
    });

    expect(view.getByTestId('home-status')).toHaveAccessibleName(
      en.home.onDuty,
    );
    expect(view.getByTestId('home-figure')).toHaveTextContent('4');
    expect(view.getByTestId('home-legend')).toHaveTextContent(/2 handled/);
    expect(view.getByTestId('home-legend')).toHaveTextContent(/1 you/);
    expect(view.getByTestId('home-legend')).toHaveTextContent(/1 spam/);
    expect(view.getByTestId('home-call-courier')).toBeOnTheScreen();
    expect(view.queryByTestId('home-call-another')).toBeNull();
    expect(mockSecure.setSecure).toHaveBeenLastCalledWith(true);

    await fireEvent.press(view.getByTestId('home-call-bank'));
    expect(await view.findByTestId('call-detail')).toBeOnTheScreen();
  });

  it('shows the latest calls even when none came in today', async () => {
    const yesterday = new Date(Date.now() - 2 * 86_400_000).toISOString();
    const { view, backend } = await home({
      calls: [aCall({ id: 'old', started_at: yesterday })],
    });

    // The fake backend does not filter by date, so today's listing is told apart by its query.
    expect(backend.listings.some(query => query.includes('from='))).toBe(true);
    expect(view.getByTestId('home-call-old')).toBeOnTheScreen();
  });

  it('takes the whole screen when a call still going needs the user', async () => {
    const { view } = await home({
      calls: [
        aCall({
          id: 'live',
          status: 'in_progress',
          outcome: null,
          headline: null,
          started_at: EARLIER,
          timings: {
            ...aCall().timings,
            escalated_at: EARLIER,
            ended_at: null,
          },
        }),
      ],
      escalations: {
        live: {
          call_id: 'live',
          status: 'live',
          title: "Can't agree without you",
          caller: 'Your bank',
          caller_label: 'Important contact',
          body: 'A payment is held.',
          established: null,
          needed: null,
          reason: 'decision_needs_the_user',
          raised_at: EARLIER,
          ended_at: null,
          delivery: 'delivered',
        },
      },
    });

    expect(view.getByTestId('home-status')).toHaveAccessibleName(
      en.home.needsYou,
    );
    expect(view.getByTestId('home-needs-you')).toHaveTextContent(
      /Answer your phone/,
    );
    expect(view.getByTestId('home-needs-you')).toHaveTextContent(/Your bank/);
    expect(view.getByTestId('home-needs-you-timer')).toHaveTextContent(
      /^1:0\d$/,
    );

    await fireEvent.press(view.getByTestId('home-open-escalation'));
    expect(await view.findByTestId('escalation-screen')).toBeOnTheScreen();
  });

  it('still says it needs the user when why cannot be read', async () => {
    const { view } = await home({
      calls: [
        aCall({
          id: 'live',
          status: 'in_progress',
          timings: {
            ...aCall().timings,
            escalated_at: EARLIER,
            ended_at: null,
          },
        }),
      ],
      escalations: { live: { status: 503, error: 'escalations_unavailable' } },
    });

    expect(view.getByTestId('home-status')).toHaveAccessibleName(
      en.home.needsYou,
    );
    expect(view.queryByTestId('home-needs-you-timer')).toBeNull();
  });

  it('is not on duty on a screening phone without the role, and says how to fix it', async () => {
    const native = new FakeNativeCallScreening();
    native.role = 'available';
    const { view } = await home({ calls: [] }, callScreeningFrom(native));

    expect(view.getByTestId('home-status')).toHaveAccessibleName(
      en.home.notOnDuty,
    );
    expect(view.getByTestId('home-not-screening')).toHaveTextContent(
      /ring you directly/,
    );
    await fireEvent.press(view.getByTestId('home-turn-on-screening'));
    expect(await view.findByTestId('screening-screen')).toBeOnTheScreen();
  });

  it('counts what a screening phone stopped, and says what it cannot do', async () => {
    const native = new FakeNativeCallScreening();
    native.role = 'held';
    const { view } = await home(
      {
        calls: [
          aCall({ id: 'a', outcome: 'rejected_by_rule', started_at: EARLIER }),
          aCall({ id: 'b', outcome: 'passed_through', started_at: EARLIER }),
        ],
      },
      callScreeningFrom(native),
    );

    expect(view.getByTestId('home-status')).toHaveAccessibleName(
      en.home.screening,
    );
    expect(view.getByTestId('home-figure')).toHaveTextContent('1');
    expect(view.getByTestId('home-legend')).toHaveTextContent(/1 rang you/);
    expect(view.getByTestId('home-screening-only')).toHaveTextContent(
      en.home.screeningOnly,
    );
  });

  it('is on duty on a screening phone whose calls also reach the assistant forwarded', async () => {
    const native = new FakeNativeCallScreening();
    native.role = 'held';
    const { view } = await home(
      { calls: [aCall({ id: 'courier', started_at: EARLIER })] },
      callScreeningFrom(native),
      true,
    );

    expect(view.getByTestId('home-status')).toHaveAccessibleName(
      en.home.onDuty,
    );
    expect(view.getByTestId('home-figure')).toHaveTextContent('1');
    expect(view.queryByTestId('home-screening-only')).toBeNull();
  });

  it('keeps what it last showed, said to be old, when the connection goes', async () => {
    const { view, backend } = await home({
      calls: [aCall({ started_at: EARLIER })],
    });
    await fireEvent.press(view.getByTestId('tab-activity'));
    await view.findByTestId('call-call-1');
    // The connection goes while Activity is open; Home's next refresh is the one that fails.
    backend.failNext('/v1/calls', {
      status: 503,
      body: { error: 'down', message: 'x' },
    });
    await fireEvent.press(view.getByTestId('tab-home'));

    expect(await view.findByTestId('home-offline')).toHaveTextContent(
      /No connection/,
    );
    expect(view.getByTestId('home-figure')).toHaveTextContent('1');
  });

  it('draws the ring first and offers another try when nothing has loaded', async () => {
    const backend = runningBackend({ startAt: null, history: { calls: [] } });
    backend.failNext('/v1/calls', {
      status: 503,
      body: { error: 'down', message: 'x' },
    });
    const view = await render(
      <SessionProvider>
        <RootNavigator screening={null} />
      </SessionProvider>,
    );

    expect(await view.findByTestId('home-problem')).toBeOnTheScreen();
    expect(view.getByTestId('home-loading')).toBeOnTheScreen();
    await fireEvent.press(view.getByTestId('home-retry'));
    await waitFor(() => {
      expect(view.getByTestId('home-status')).toHaveAccessibleName(
        en.home.waiting,
      );
    });
  });
});

describe('refreshing Home', () => {
  it('lets a slow load finish rather than starting it again every interval', async () => {
    fakeOnly('setInterval', 'clearInterval');
    try {
      const release: (() => void)[] = [];
      const api = {
        calls: jest.fn(
          () =>
            new Promise(resolve => {
              release.push(() => {
                resolve({ calls: [], next_cursor: null });
              });
            }),
        ),
      } as unknown as ApiClient;
      const view = await renderHook(() => useHome(api, null, 'u1'));

      await act(async () => {
        jest.advanceTimersByTime(REFRESH_EVERY_MS * 2);
      });
      await act(async () => {
        release.forEach(finish => finish());
      });
      await act(async () => {
        release.forEach(finish => finish());
      });

      expect(view.result.current.snapshot).not.toBeNull();
      expect(api.calls).toHaveBeenCalledTimes(2);
    } finally {
      jest.useRealTimers();
    }
  });
});

describe('the remembered snapshot', () => {
  it('is never shown to a session whose account is not known yet', async () => {
    runningBackend({ startAt: null, history: { calls: [aCall()] } });
    const api = new ApiClient({
      accessToken: () => 'a-token',
      renew: async () => null,
      onSignedOut: () => undefined,
    });
    const first = await renderHook(() => useHome(api, null, null));
    await waitFor(() => {
      expect(first.result.current.snapshot).not.toBeNull();
    });
    await first.unmount();
    globalThis.fetch = (async () => {
      throw new TypeError('Network request failed');
    }) as unknown as typeof fetch;

    const second = await renderHook(() => useHome(api, null, null));

    expect(second.result.current.snapshot).toBeNull();
  });
});

describe('the facts behind it', () => {
  it('orders the states: needed, then the phone, then whether calls have come', () => {
    expect(
      homeState({
        escalatedCallId: 'x',
        screeningRole: 'available',
        anyCalls: false,
        callsForwarded: false,
      }),
    ).toBe('needs-you');
    expect(
      homeState({
        escalatedCallId: null,
        screeningRole: 'failed',
        anyCalls: true,
        callsForwarded: false,
      }),
    ).toBe('not-on-duty');
    expect(
      homeState({
        escalatedCallId: null,
        screeningRole: 'held',
        anyCalls: false,
        callsForwarded: false,
      }),
    ).toBe('screening');
    expect(
      homeState({
        escalatedCallId: null,
        screeningRole: 'failed',
        anyCalls: false,
        callsForwarded: true,
      }),
    ).toBe('first-day');
    expect(
      homeState({
        escalatedCallId: null,
        screeningRole: null,
        anyCalls: true,
        callsForwarded: false,
      }),
    ).toBe('on-duty');
    expect(
      homeState({
        escalatedCallId: null,
        screeningRole: null,
        anyCalls: false,
        callsForwarded: false,
      }),
    ).toBe('first-day');
  });

  it('counts a call once, by what happened to it', () => {
    const counts = tally([
      aCall({ outcome: 'resolved_by_agent' }),
      aCall({ outcome: 'unanswered_escalation' }),
      aCall({ outcome: 'passed_through' }),
      aCall({ outcome: 'rejected_by_rule', human_joined: false }),
      aCall({ outcome: 'failed' }),
      aCall({ outcome: 'caller_hung_up', human_joined: true }),
    ]);
    expect(counts).toEqual({ total: 6, handled: 1, you: 3, turnedAway: 1 });
    expect(ringParts(counts)).toEqual({
      handled: 1 / 6,
      you: 3 / 6,
      turnedAway: 1 / 6,
    });
    expect(ringParts(tally([]))).toEqual({ handled: 0, you: 0, turnedAway: 0 });
  });

  it('reads midnight and elapsed time as the phone does', () => {
    expect(
      new Date(startOfToday(new Date(2026, 8, 13, 15, 30))).getHours(),
    ).toBe(0);
    expect(
      elapsed('2026-09-13T10:00:00Z', new Date('2026-09-13T10:01:28Z')),
    ).toBe('1:28');
    expect(
      elapsed('2026-09-13T10:00:00Z', new Date('2026-09-13T09:00:00Z')),
    ).toBe('0:00');
  });
});
