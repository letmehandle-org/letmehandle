/**
 * Call history: the list, one call's summary, what was said, and deleting.
 *
 * The whole tree against a backend that remembers, because what matters here happens across
 * requests — a filter asking the server rather than hiding rows, a deleted call leaving the list.
 */
import { fireEvent, render, waitFor } from '@testing-library/react-native';
import React from 'react';

import { App } from '../App';
import { en } from '../i18n/locales/en';
import { aCall, runningBackend, type HistorySetup } from './support/backend';

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

const YESTERDAY = new Date(Date.now() - 86_400_000).toISOString();

async function openActivity(history: HistorySetup) {
  const backend = runningBackend({ startAt: null, history });
  const view = await render(<App />);
  await waitFor(() => {
    expect(view.getByTestId('home-screen')).toBeOnTheScreen();
  });
  await fireEvent.press(view.getByTestId('tab-activity'));
  await waitFor(() => {
    expect(view.getByTestId('activity-screen')).toBeOnTheScreen();
  });
  return { backend, view };
}

async function openCall(view: View, id: string): Promise<void> {
  await fireEvent.press(await view.findByTestId(`call-${id}`));
  await waitFor(() => {
    expect(view.getByTestId('call-headline')).toBeOnTheScreen();
  });
}

beforeEach(() => {
  mockSecure.setSecure.mockClear();
});

describe('the list', () => {
  it('shows each call under its day, with what happened and when', async () => {
    const { view } = await openActivity({
      calls: [
        aCall({ id: 'courier' }),
        aCall({
          id: 'bank',
          caller: {
            category: 'financial',
            display_name: 'Your bank',
            number_withheld: false,
          },
          outcome: 'handed_to_user',
          human_joined: true,
          headline: null,
          duration_seconds: 302,
        }),
        aCall({
          id: 'old',
          started_at: YESTERDAY,
          duration_seconds: 41,
          headline: 'Order query',
        }),
      ],
    });

    const courier = await view.findByTestId('call-courier');
    expect(courier).toHaveTextContent(/Delivery/);
    expect(courier).toHaveTextContent(
      /Your parcel will be left at the gate before 6 pm\. · 3 min/,
    );
    expect(view.getByTestId('call-bank')).toHaveTextContent(/Your bank/);
    expect(view.getByTestId('call-bank')).toHaveTextContent(
      /You joined · 5 min/,
    );
    expect(view.getByTestId('call-old')).toHaveTextContent(
      /Order query · 41 s/,
    );
    expect(view.getByText(en.activity.today)).toBeOnTheScreen();
    expect(view.getByText(en.activity.yesterday)).toBeOnTheScreen();
  });

  it('asks the server for a filter rather than hiding rows', async () => {
    const { backend, view } = await openActivity({
      calls: [
        aCall({ id: 'settled' }),
        aCall({ id: 'joined', human_joined: true, outcome: 'handed_to_user' }),
      ],
    });
    await view.findByTestId('call-settled');

    await fireEvent.press(view.getByTestId('activity-filter-joined'));
    await waitFor(() => {
      expect(view.queryByTestId('call-settled')).toBeNull();
    });
    expect(view.getByTestId('call-joined')).toBeOnTheScreen();
    expect(backend.listings).toContain('human_joined=true');
    expect(view.getByTestId('activity-filter-joined')).toBeChecked();

    await fireEvent.press(view.getByTestId('activity-filter-refused'));
    expect(await view.findByText(en.activity.emptyFiltered)).toBeOnTheScreen();
    expect(backend.listings).toContain('outcome=rejected_by_rule');
  });

  it('is a designed empty state when there are no calls', async () => {
    const { view } = await openActivity({ calls: [] });
    expect(await view.findByTestId('activity-empty')).toHaveTextContent(
      en.activity.empty,
    );
  });

  it('loads older calls a page at a time', async () => {
    const { view } = await openActivity({
      calls: [aCall({ id: 'a' }), aCall({ id: 'b' }), aCall({ id: 'c' })],
      pageSize: 2,
    });
    await view.findByTestId('call-b');
    expect(view.queryByTestId('call-c')).toBeNull();

    await fireEvent.press(view.getByTestId('activity-more'));
    expect(await view.findByTestId('call-c')).toBeOnTheScreen();
    expect(view.queryByTestId('activity-more')).toBeNull();
  });

  it('says so when the calls cannot be loaded, and tries again', async () => {
    const backend = runningBackend({
      startAt: null,
      history: { calls: [aCall()] },
    });
    backend.failNext('/v1/calls', {
      status: 500,
      body: { error: 'internal_error', message: 'x' },
    });
    const view = await render(<App />);
    await view.findByTestId('home-screen');
    await fireEvent.press(view.getByTestId('tab-activity'));

    expect(await view.findByTestId('activity-problem')).toBeOnTheScreen();
    await fireEvent.press(view.getByTestId('activity-retry'));
    expect(await view.findByTestId('call-call-1')).toBeOnTheScreen();
  });

  it('keeps calls out of screenshots while the list is open', async () => {
    const { view } = await openActivity({ calls: [] });
    await view.findByTestId('activity-empty');
    expect(mockSecure.setSecure).toHaveBeenLastCalledWith(true);

    await fireEvent.press(view.getByTestId('tab-home'));
    expect(mockSecure.setSecure).toHaveBeenLastCalledWith(false);
  });
});

describe('one call', () => {
  it('shows the summary, what was settled, and where the words are', async () => {
    const { view } = await openActivity({ calls: [aCall()] });
    await openCall(view, 'call-1');

    expect(view.getByTestId('call-headline')).toHaveTextContent(
      'Your parcel will be left at the gate before 6 pm.',
    );
    expect(view.getByTestId('call-outcome')).toHaveTextContent(
      en.call.outcome.resolved_by_agent,
    );
    expect(
      view.getAllByText(en.call.intent.delivery_in_progress).length,
    ).toBeGreaterThan(0);
    expect(view.getByText('Gate, with the guard')).toBeOnTheScreen();
    expect(view.getByTestId('call-open-transcript')).toHaveTextContent(
      /Kept until 19 September/,
    );
  });

  it('draws the timings as one strip when the user joined, and says why it called', async () => {
    const { view } = await openActivity({
      calls: [
        aCall({
          human_joined: true,
          outcome: 'handed_to_user',
          escalation_reason: 'action_not_authorised',
          timings: {
            received_at: '2026-09-13T09:12:00Z',
            answered_at: '2026-09-13T09:12:00Z',
            escalated_at: '2026-09-13T09:13:40Z',
            human_joined_at: '2026-09-13T09:14:06Z',
            ended_at: '2026-09-13T09:17:02Z',
          },
        }),
      ],
    });
    await openCall(view, 'call-1');

    expect(view.getByTestId('call-timeline-escalated')).toHaveTextContent(
      /\+1:40/,
    );
    expect(view.getByTestId('call-timeline-joined')).toHaveTextContent(
      /\+2:06/,
    );
    expect(view.getByTestId('call-reason')).toHaveTextContent(
      /You haven't allowed what they asked/,
    );
  });

  it('is honest that a refused call kept nothing', async () => {
    const { view } = await openActivity({
      calls: [
        aCall({
          caller: {
            category: 'spam',
            display_name: null,
            number_withheld: false,
          },
          outcome: 'rejected_by_rule',
          handling: null,
          headline: 'Known spam. Turned away before it rang.',
          details: [],
          transcript_available: false,
          transcript_expires_at: null,
        }),
      ],
    });
    await openCall(view, 'call-1');

    expect(view.getByTestId('call-nothing-kept')).toHaveTextContent(
      en.call.refusedNothing,
    );
    expect(view.queryByTestId('call-open-transcript')).toBeNull();
  });

  it('offers the live escalation while a call that needs the user is still going, and no delete', async () => {
    const { view } = await openActivity({
      calls: [
        aCall({
          status: 'in_progress',
          outcome: null,
          headline: null,
          transcript_available: false,
          handling: 'assistant',
          timings: {
            ...aCall().timings,
            escalated_at: new Date().toISOString(),
            ended_at: null,
          },
        }),
      ],
      escalations: {
        'call-1': {
          call_id: 'call-1',
          status: 'live',
          title: "You haven't let it confirm payments",
          caller: 'Your bank',
          caller_label: 'Important contact',
          body: 'It needs you.',
          established: '42,000 attempted in Pune, 08:58',
          needed: 'Was it you? Yes or no',
          reason: 'action_not_authorised',
          raised_at: new Date().toISOString(),
          ended_at: null,
          delivery: 'delivered',
        },
      },
    });
    await openCall(view, 'call-1');
    expect(view.getByTestId('call-headline')).toHaveTextContent(
      en.call.inProgress,
    );
    expect(view.queryByTestId('call-delete')).toBeNull();

    await fireEvent.press(view.getByTestId('call-open-escalation'));
    expect(await view.findByTestId('escalation-status')).toHaveTextContent(
      en.escalation.live,
    );
    expect(view.getByTestId('escalation-why')).toHaveTextContent(
      /confirm payments/,
    );
    expect(view.getByTestId('escalation-answer')).toHaveTextContent(
      en.escalation.answerToJoin,
    );
  });

  it('says a deleted call is gone rather than failing', async () => {
    const { backend, view } = await openActivity({ calls: [aCall()] });
    await view.findByTestId('call-call-1');
    backend.failNext('/v1/calls/call-1', {
      status: 404,
      body: { error: 'call_not_found', message: 'x' },
    });
    await fireEvent.press(view.getByTestId('call-call-1'));
    expect(await view.findByTestId('call-gone')).toHaveTextContent(
      en.call.gone,
    );
  });

  it('offers another try when the call cannot be loaded', async () => {
    const { backend, view } = await openActivity({ calls: [aCall()] });
    await view.findByTestId('call-call-1');
    backend.failNext('/v1/calls/call-1', {
      status: 500,
      body: { error: 'internal_error', message: 'x' },
    });
    await fireEvent.press(view.getByTestId('call-call-1'));

    expect(await view.findByTestId('call-problem')).toBeOnTheScreen();
    await fireEvent.press(view.getByTestId('call-retry'));
    expect(await view.findByTestId('call-headline')).toBeOnTheScreen();
  });
});

describe('an escalation opened after its call', () => {
  const ended = {
    call_id: 'call-1',
    status: 'ended' as const,
    title: 'Payments not authorised',
    caller: null,
    caller_label: 'Unknown caller',
    body: 'It asked you about a payment.',
    established: null,
    needed: null,
    reason: 'action_not_authorised' as const,
    raised_at: new Date().toISOString(),
    ended_at: new Date().toISOString(),
    delivery: 'delivered' as const,
  };

  async function openEscalation(held: HistorySetup['escalations']) {
    const opened = await openActivity({
      calls: [
        aCall({
          status: 'in_progress',
          timings: {
            ...aCall().timings,
            escalated_at: new Date().toISOString(),
          },
        }),
      ],
      escalations: held,
    });
    await openCall(opened.view, 'call-1');
    await fireEvent.press(opened.view.getByTestId('call-open-escalation'));
    return opened;
  }

  it('shows that it finished and leads to the summary, not a call to join', async () => {
    const { view } = await openEscalation({ 'call-1': ended });

    expect(await view.findByTestId('escalation-status')).toHaveTextContent(
      en.escalation.ended,
    );
    expect(view.queryByTestId('escalation-answer')).toBeNull();
    expect(view.getByText('It asked you about a payment.')).toBeOnTheScreen();
    await fireEvent.press(view.getByTestId('escalation-open-summary'));
    expect(await view.findByTestId('call-detail')).toBeOnTheScreen();
  });

  it('still leads to the summary where escalations cannot be read', async () => {
    const { view } = await openEscalation({
      'call-1': { status: 503, error: 'escalations_unavailable' },
    });
    expect(await view.findByTestId('escalation-problem')).toBeOnTheScreen();
    await fireEvent.press(view.getByTestId('escalation-fallback-summary'));
    expect(await view.findByTestId('call-detail')).toBeOnTheScreen();
  });
});

describe('deleting a call', () => {
  it('names what goes, deletes on confirming, and the list no longer shows it', async () => {
    const { backend, view } = await openActivity({
      calls: [aCall(), aCall({ id: 'other' })],
    });
    await openCall(view, 'call-1');

    await fireEvent.press(view.getByTestId('call-delete'));
    expect(view.getByTestId('call-delete-sheet')).toHaveTextContent(
      /The summary/,
    );
    expect(view.getByTestId('call-delete-sheet')).toHaveTextContent(
      /What was said/,
    );
    await fireEvent.press(view.getByTestId('call-delete-sheet-confirm'));

    expect(await view.findByTestId('activity-screen')).toBeOnTheScreen();
    await waitFor(() => {
      expect(view.queryByTestId('call-call-1')).toBeNull();
    });
    expect(view.getByTestId('call-other')).toBeOnTheScreen();
    expect(backend.calls().map(call => call.id)).toEqual(['other']);
  });

  it('keeps the call when asked to, and when the delete fails', async () => {
    const { backend, view } = await openActivity({ calls: [aCall()] });
    await openCall(view, 'call-1');

    await fireEvent.press(view.getByTestId('call-delete'));
    await fireEvent.press(view.getByTestId('call-delete-sheet-keep'));
    expect(backend.calls()).toHaveLength(1);

    await fireEvent.press(view.getByTestId('call-delete'));
    backend.failNext('/v1/calls/call-1', {
      status: 500,
      body: { error: 'internal_error', message: 'x' },
    });
    await fireEvent.press(view.getByTestId('call-delete-sheet-confirm'));
    expect(await view.findByText(en.call.deleteFailed)).toBeOnTheScreen();
    expect(backend.calls()).toHaveLength(1);
  });
});

describe('what was said', () => {
  const transcript = {
    call_id: 'call-1',
    transcript_retention_days: 7,
    transcript_expires_at: '2026-09-19T10:24:00Z',
    entries: [
      {
        speaker: 'caller' as const,
        text: "I'm at the gate.",
        said_at: '2026-09-13T10:24:00Z',
      },
      {
        speaker: 'agent' as const,
        text: 'Leave it with the guard.',
        said_at: '2026-09-13T10:24:05Z',
      },
    ],
  };

  async function openTranscript(held: HistorySetup['transcripts']) {
    const opened = await openActivity({ calls: [aCall()], transcripts: held });
    await openCall(opened.view, 'call-1');
    await fireEvent.press(opened.view.getByTestId('call-open-transcript'));
    return opened;
  }

  it('shows each line by who said it, and when the words go', async () => {
    const { view } = await openTranscript({ 'call-1': transcript });

    expect(await view.findByTestId('transcript-expiry')).toHaveTextContent(
      'Deleted on 19 September. The summary stays.',
    );
    expect(view.getByTestId('transcript-line-caller')).toHaveTextContent(
      "I'm at the gate.",
    );
    expect(view.getByTestId('transcript-line-agent')).toHaveAccessibleName(
      'Your assistant: Leave it with the guard.',
    );
  });

  it('keeps three screens of what callers said out of screenshots, until the last one closes', async () => {
    const { view } = await openTranscript({ 'call-1': transcript });
    await view.findByTestId('transcript-expiry');
    expect(mockSecure.setSecure).toHaveBeenCalledTimes(1);

    await fireEvent.press(view.getByTestId('transcript-screen-back'));
    await fireEvent.press(await view.findByTestId('call-detail-back'));
    await view.findByTestId('activity-screen');
    expect(mockSecure.setSecure).toHaveBeenCalledTimes(1);
  });

  it('presents purged words as the retention working, with a way to change it', async () => {
    const { view } = await openTranscript({
      'call-1': { status: 410, error: 'transcript_purged' },
    });

    expect(await view.findByTestId('transcript-purged')).toHaveTextContent(
      /These words were deleted, as you set: 7 days\./,
    );
    await fireEvent.press(view.getByTestId('transcript-open-privacy'));
    expect(await view.findByTestId('settings-privacy')).toBeOnTheScreen();
  });

  it('says there never were words for a call nobody spoke on', async () => {
    const { view } = await openTranscript({
      'call-1': { status: 404, error: 'transcript_not_recorded' },
    });
    expect(
      await view.findByTestId('transcript-not-recorded'),
    ).toHaveTextContent(en.transcript.notRecorded);
  });

  it('offers another try for anything else', async () => {
    const { backend, view } = await openActivity({
      calls: [aCall()],
      transcripts: { 'call-1': transcript },
    });
    await openCall(view, 'call-1');
    backend.failNext('/v1/calls/call-1/transcript', {
      status: 500,
      body: { error: 'internal_error', message: 'x' },
    });
    await fireEvent.press(view.getByTestId('call-open-transcript'));

    expect(await view.findByTestId('transcript-problem')).toBeOnTheScreen();
    await fireEvent.press(view.getByTestId('transcript-retry'));
    expect(await view.findByTestId('transcript-expiry')).toBeOnTheScreen();
  });
});
