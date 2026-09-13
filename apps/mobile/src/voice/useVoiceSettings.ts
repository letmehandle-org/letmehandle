/**
 * The voice, loaded and changed.
 *
 * Kept out of the screen so that the screen is a rendering of three states rather than a
 * component that also knows how to fetch. Not held in a provider either, unlike the
 * preferences: nothing outside this screen reads the voice today, and a second application-wide
 * load on every cold start would be paid by everybody to save a fetch for the few who open it.
 */
import { useCallback, useMemo, useState } from 'react';

import type { VoiceCatalogue, VoiceSelection } from '../api/voice';
import { useLoaded } from '../api/useLoaded';
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
  const load = useCallback(async () => {
    const [catalogue, selection] = await Promise.all([
      api.voices(),
      api.voiceSelection(),
    ]);
    return { catalogue, selection };
  }, [api]);
  const { loaded, retry } = useLoaded(load);
  // The server's answer to the latest choice, which is the voice a call would actually use.
  const [chosen, setChosen] = useState<VoiceSelection | null>(null);

  const state = useMemo<VoiceState>(() => {
    if (loaded.state === 'loading') {
      return { status: 'loading' };
    }
    if (loaded.state === 'failed') {
      return { status: 'unavailable' };
    }
    return {
      status: 'ready',
      catalogue: loaded.value.catalogue,
      selection: chosen ?? loaded.value.selection,
    };
  }, [loaded, chosen]);

  const choose = useCallback(
    async (voiceId: string | null): Promise<void> => {
      setChosen(await api.chooseVoice(voiceId));
    },
    [api],
  );

  const reload = useCallback((): void => {
    setChosen(null);
    retry();
  }, [retry]);

  return { state, reload, choose };
}
