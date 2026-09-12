/**
 * Call screening, as somebody using the app meets it.
 *
 * What is worth proving: the screen explains the role before asking for it; saying no is a
 * complete answer that leaves every call ringing and the question open; the handset is given
 * the user's rules and given them again when they change; what the handset observed reaches the
 * backend; and a handset that cannot screen shows none of it.
 */
import { fireEvent, render, waitFor } from '@testing-library/react-native';
import React from 'react';
import { AppState, PermissionsAndroid } from 'react-native';

import { SessionProvider } from '../auth/SessionProvider';
import { callScreeningFrom, type CallScreening } from '../calls/callScreening';
import { initialiseI18n } from '../i18n';
import { en } from '../i18n/locales/en';
import { RootNavigator } from '../navigation/RootNavigator';
import { runningBackend, type RunningBackend } from './support/backend';
import { FakeNativeCallScreening } from './support/nativeCallScreening';

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

type View = Awaited<ReturnType<typeof render>>;

const SCREENED = {
  event_id: 'event-0001',
  call_id: 'call-0001',
  kind: 'incoming',
  occurred_at: '2026-09-13T11:00:00Z',
  caller_number: '+12025550145',
  screening: 'silence',
};

beforeAll(async () => {
  await initialiseI18n('en');
});

let checkPermission: jest.SpyInstance;
let requestPermission: jest.SpyInstance;

beforeEach(() => {
  checkPermission = jest
    .spyOn(PermissionsAndroid, 'check')
    .mockResolvedValue(false);
  requestPermission = jest
    .spyOn(PermissionsAndroid, 'request')
    .mockResolvedValue(PermissionsAndroid.RESULTS.DENIED);
});

afterEach(() => {
  jest.restoreAllMocks();
});

async function signedIn(screening: CallScreening | null): Promise<View> {
  const view = await render(
    <SessionProvider>
      <RootNavigator screening={screening} />
    </SessionProvider>,
  );
  await waitFor(() => {
    expect(view.getByTestId('home-screen')).toBeOnTheScreen();
  });
  return view;
}

async function openScreening(
  native: FakeNativeCallScreening,
): Promise<{ view: View; backend: RunningBackend }> {
  const backend = runningBackend();
  const view = await signedIn(callScreeningFrom(native));
  await fireEvent.press(view.getByTestId('open-settings'));
  await fireEvent.press(view.getByTestId('settings-open-call-screening'));
  await waitFor(() => {
    expect(view.getByTestId('screening-screen')).toBeOnTheScreen();
  });
  return { view, backend };
}

describe('a handset that cannot screen calls', () => {
  it('offers nothing about screening', async () => {
    runningBackend();
    const view = await signedIn(null);
    await fireEvent.press(view.getByTestId('open-settings'));

    expect(view.getByTestId('settings-screen')).toBeOnTheScreen();
    expect(view.queryByTestId('settings-open-call-screening')).toBeNull();
  });
});

describe('asking for the role', () => {
  it('explains what it will and will not do before asking', async () => {
    const native = new FakeNativeCallScreening();
    const { view } = await openScreening(native);

    expect(await view.findByText(en.screening.what)).toBeOnTheScreen();
    expect(view.getByText(en.screening.whatNot)).toBeOnTheScreen();
    expect(view.getByText(en.screening.offExplained)).toBeOnTheScreen();
    expect(native.requests).toBe(0);
  });

  it('takes no for an answer, says what is then true, and can ask again', async () => {
    const native = new FakeNativeCallScreening();
    const { view } = await openScreening(native);

    await fireEvent.press(await view.findByTestId('screening-turn-on'));

    expect(await view.findByText(en.screening.declined)).toBeOnTheScreen();
    expect(view.getByText(en.screening.notAskedAgain)).toBeOnTheScreen();

    native.requestOutcome = 'held';
    await fireEvent.press(view.getByTestId('screening-turn-on'));

    expect(await view.findByTestId('screening-held')).toBeOnTheScreen();
    expect(native.requests).toBe(2);
  });

  it('says so plainly where the phone cannot grant it', async () => {
    const native = new FakeNativeCallScreening();
    native.role = 'unavailable';
    const { view } = await openScreening(native);

    expect(await view.findByText(en.screening.unavailable)).toBeOnTheScreen();
    expect(view.queryByTestId('screening-turn-on')).toBeNull();
  });

  it('says it could not tell rather than guessing', async () => {
    const native = new FakeNativeCallScreening();
    native.role = 'sometimes';
    const { view } = await openScreening(native);

    expect(await view.findByText(en.screening.failed)).toBeOnTheScreen();
  });

  it('treats a request that could not be shown as not knowing', async () => {
    const native = new FakeNativeCallScreening();
    native.requestOutcome = 'on a screen that has gone';
    const { view } = await openScreening(native);

    await fireEvent.press(await view.findByTestId('screening-turn-on'));

    expect(await view.findByText(en.screening.failed)).toBeOnTheScreen();
  });
});

describe('call activity', () => {
  it('is offered only once screening is on, and declining it leaves screening working', async () => {
    const native = new FakeNativeCallScreening();
    native.role = 'held';
    const { view } = await openScreening(native);

    expect(await view.findByTestId('screening-held')).toBeOnTheScreen();
    await fireEvent.press(view.getByTestId('screening-activity-turn-on'));

    expect(requestPermission).toHaveBeenCalledWith(
      PermissionsAndroid.PERMISSIONS.READ_PHONE_STATE,
      expect.objectContaining({ message: en.screening.activityRationale }),
    );
    expect(await view.findByTestId('screening-activity-off')).toBeOnTheScreen();
    expect(view.getByTestId('screening-held')).toBeOnTheScreen();
  });

  it('says when it is on', async () => {
    const native = new FakeNativeCallScreening();
    native.role = 'held';
    const { view } = await openScreening(native);
    requestPermission.mockResolvedValue(PermissionsAndroid.RESULTS.GRANTED);

    await fireEvent.press(
      await view.findByTestId('screening-activity-turn-on'),
    );

    expect(
      await view.findByTestId('screening-activity-granted'),
    ).toBeOnTheScreen();
  });

  it('reads a permission already granted', async () => {
    checkPermission.mockResolvedValue(true);
    const native = new FakeNativeCallScreening();
    native.role = 'held';
    const { view } = await openScreening(native);

    expect(
      await view.findByTestId('screening-activity-granted'),
    ).toBeOnTheScreen();
  });

  it('counts a request that fails as not granted', async () => {
    const native = new FakeNativeCallScreening();
    native.role = 'held';
    const { view } = await openScreening(native);
    requestPermission.mockRejectedValue(new Error('no activity'));

    await fireEvent.press(
      await view.findByTestId('screening-activity-turn-on'),
    );

    expect(await view.findByTestId('screening-activity-off')).toBeOnTheScreen();
  });
});

describe('keeping the handset in step', () => {
  it('gives the handset the rules when the app starts, and again when they change', async () => {
    const native = new FakeNativeCallScreening();
    const backend = runningBackend();
    const view = await signedIn(callScreeningFrom(native));

    await waitFor(() => {
      expect(native.snapshots).toHaveLength(1);
    });
    expect(native.latestSnapshot().anonymous_posture).toBe('handle_with_agent');

    await fireEvent.press(view.getByTestId('open-settings'));
    await fireEvent.press(view.getByTestId('settings-open-call_handling'));
    await fireEvent.press(await view.findByTestId('handling-anonymous-reject'));
    await fireEvent.press(view.getByTestId('settings-save'));

    await waitFor(() => {
      expect(native.latestSnapshot().anonymous_posture).toBe('reject');
    });
    expect(backend.patches).toHaveLength(1);
  });

  it('says on the screening screen when the rules could not be saved to the handset', async () => {
    const native = new FakeNativeCallScreening();
    native.refuseNextSnapshot = true;
    const { view } = await openScreening(native);

    expect(
      await view.findByTestId('screening-rules-not-saved'),
    ).toBeOnTheScreen();
  });

  it('reports what the handset observed at start and whenever it records more', async () => {
    const native = new FakeNativeCallScreening();
    native.pending = [SCREENED];
    const backend = runningBackend();
    await signedIn(callScreeningFrom(native));

    await waitFor(() => {
      expect(backend.reports.map(report => report.event_id)).toEqual([
        'event-0001',
      ]);
    });
    expect(native.pending).toEqual([]);

    native.record({
      event_id: 'event-0002',
      call_id: 'call-0001',
      kind: 'ended',
      occurred_at: '2026-09-13T11:00:30Z',
      ending: 'missed',
    });

    await waitFor(() => {
      expect(backend.reports).toHaveLength(2);
    });
    expect(backend.reports[1].ending).toBe('missed');
  });

  it('reports again when the app comes back to the front', async () => {
    // A call that arrived while the app was in the background and nothing announced it: the
    // native side records it regardless, and returning to the app is when it is sent.
    const listeners: ((state: string) => void)[] = [];
    jest
      .spyOn(AppState, 'addEventListener')
      .mockImplementation((_type, listener) => {
        listeners.push(listener as (state: string) => void);
        return { remove: () => undefined };
      });
    const native = new FakeNativeCallScreening();
    const backend = runningBackend();
    await signedIn(callScreeningFrom(native));
    await waitFor(() => {
      expect(listeners.length).toBeGreaterThan(0);
    });

    native.pending = [SCREENED];
    listeners.forEach(listener => listener('background'));
    expect(backend.reports).toEqual([]);
    listeners.forEach(listener => listener('active'));

    await waitFor(() => {
      expect(backend.reports).toHaveLength(1);
    });
  });

  it('keeps what could not be reported for the next time', async () => {
    const native = new FakeNativeCallScreening();
    native.pending = [SCREENED];
    const backend = runningBackend();
    backend.failNextReport();
    await signedIn(callScreeningFrom(native));

    await waitFor(() => {
      expect(native.pending).toHaveLength(1);
    });
    native.record();

    await waitFor(() => {
      expect(backend.reports).toHaveLength(1);
    });
    expect(native.pending).toEqual([]);
  });

  it('forgets the account on the handset when somebody signs out', async () => {
    const native = new FakeNativeCallScreening();
    runningBackend();
    const view = await signedIn(callScreeningFrom(native));
    await fireEvent.press(view.getByTestId('open-profile'));

    await fireEvent.press(await view.findByTestId('profile-sign-out'));

    await waitFor(() => {
      expect(native.forgotten).toBe(1);
    });
    expect(native.snapshots).toEqual([]);
  });
});
