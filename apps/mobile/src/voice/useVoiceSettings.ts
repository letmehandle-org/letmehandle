/**
 * The voice, loaded and changed.
 *
 * Kept out of the screen so that the screen is a rendering of three states rather than a
 * component that also knows how to fetch. Not held in a provider either, unlike the
 * preferences: nothing outside this screen reads the voice today, and a second application-wide
 * load on every cold start would be paid by everybody to save a fetch for the few who open it.
 */
import { useCallback, useEffect, useState } from 'react';

import type { VoiceCatalogue, VoiceSelection } from '../api/voice';
import { useSession } from '../auth/SessionProvider';

export type VoiceState =
  | { readonly status: 'loading' }
  | { readonly status: 'unavailable' }
  | {
      readonly status: 'ready';
      readonly catalogue: VoiceCatalogue;
      readonly selection: VoiceSelection;
    };

export interface VoiceSettings {
  readonly state: VoiceState;
  /** Try the load again after it failed. */
  reload(): void;
  /**
   * Choose a voice, or clear the choice with null.
   *
   * Rejects when the server refuses, having changed nothing. The caller is left to say so:
   * a refusal that is swallowed here becomes a control that springs back for no stated reason.
   */
  choose(voiceId: string | null): Promise<void>;
}

export function useVoiceSettings(): VoiceSettings {
  const { api } = useSession();
  const [state, setState] = useState<VoiceState>({ status: 'loading' });
  // Bumped to ask for another go. A boolean would not fire the effect a second time after a
  // failure, which is the only moment anybody presses the button.
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;

    const load = async (): Promise<void> => {
      try {
        // Together, because the screen cannot be drawn from either alone: the catalogue says
        // what may be offered and the selection says what is taken.
        const [catalogue, selection] = await Promise.all([
          api.voices(),
          api.voiceSelection(),
        ]);
        if (!cancelled) {
          setState({ status: 'ready', catalogue, selection });
        }
      } catch {
        // Which failure it was does not change what can be offered here. Retrying is the only
        // useful answer.
        if (!cancelled) {
          setState({ status: 'unavailable' });
        }
      }
    };

    setState({ status: 'loading' });
    load().catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [api, attempt]);

  const choose = useCallback(
    async (voiceId: string | null): Promise<void> => {
      const selection = await api.chooseVoice(voiceId);
      // Not optimistic, unlike a preference. The server answers with the voice a call would
      // actually use, which is the part of this screen worth being right rather than quick.
      setState(current =>
        current.status === 'ready' ? { ...current, selection } : current,
      );
    },
    [api],
  );

  const reload = useCallback((): void => {
    setAttempt(current => current + 1);
  }, []);

  return { state, reload, choose };
}
