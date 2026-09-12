import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { describeFailure } from '../api/messages';
import type { VoiceCatalogue, VoiceSelection } from '../api/voice';
import { Button } from '../components/Button';
import { Choice } from '../components/Choice';
import { Notice } from '../components/Notice';

interface Props {
  readonly catalogue: VoiceCatalogue;
  readonly selection: VoiceSelection;
  readonly onChoose: (voiceId: string | null) => Promise<void>;
}

/**
 * No choice at all, as a value a list can hold.
 *
 * The wire says null and a list of radios cannot, so the two are translated at the edge of this
 * file. An empty string cannot collide with a voice: an id is what the provider is asked for a
 * voice by, and no provider offers one that is nothing.
 */
const NO_CHOICE = '';

/**
 * Which voice answers, chosen from everything on offer.
 *
 * Every voice is listed rather than hidden behind a picker, for the reason every other choice
 * in this application is: this is what strangers hear when the assistant speaks for somebody,
 * and an option behind a tap is one people accept without having read the alternatives.
 *
 * What is drawn here is what the provider declared it can do, and nothing else (D-009). There
 * is no voice-training flow, because no shipped provider can clone a voice — not a disabled
 * one, not one behind a flag. There is no play control either, and the honest reason is in
 * `preview.ts`: the provider declares it has no samples, and this client has no route to fetch
 * one with, so a button here could only be wired to a request that 404s.
 */
export function VoiceEditor({
  catalogue,
  selection,
  onChoose,
}: Props): React.JSX.Element {
  const { t } = useTranslation();

  const [chosen, setChosen] = useState<string>(
    selection.persona_voice_id ?? NO_CHOICE,
  );
  const [problem, setProblem] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  const stored = selection.persona_voice_id ?? NO_CHOICE;

  const submit = (): void => {
    setBusy(true);
    setProblem(null);

    onChoose(chosen === NO_CHOICE ? null : chosen)
      .then(() => {
        setSaved(true);
      })
      .catch((error: unknown) => {
        // Put back rather than left where the user moved it. The server kept the old voice, and
        // a list still showing the refused one would be this screen lying about what callers
        // hear.
        setChosen(stored);
        setProblem(
          describeFailure(error, t, { refused: t('voice.saveFailed') }),
        );
      })
      .finally(() => {
        setBusy(false);
      });
  };

  return (
    <>
      <Notice
        tone={isIgnoringTheChoice(selection) ? 'problem' : 'quiet'}
        message={t(
          isIgnoringTheChoice(selection)
            ? 'voice.notYourChoice'
            : 'voice.inUse',
          { voice: nameOf(catalogue, selection.resolved_voice_id) },
        )}
        testID="voice-in-use"
      />

      <Choice
        label={t('voice.choose')}
        value={chosen}
        options={[
          { value: NO_CHOICE, label: t('voice.default') },
          ...catalogue.voices.map(voice => ({
            value: voice.id,
            label: voice.name,
          })),
        ]}
        onChange={value => {
          setChosen(value);
          setSaved(false);
        }}
        testID="voice-option"
      />

      {saved && <Notice message={t('voice.saved')} testID="voice-saved" />}
      {problem !== null && (
        <Notice tone="problem" message={problem} testID="voice-problem" />
      )}

      <Button
        label={t('common.save')}
        onPress={submit}
        busy={busy}
        disabled={chosen === stored}
        testID="voice-save"
      />
    </>
  );
}

/**
 * Whether the voice answering calls is not the one that was asked for.
 *
 * These differ only when a chosen voice has become unavailable and the fallback chain has
 * quietly stepped past it. Somebody who picked a voice deserves to be told that, rather than
 * finding out from a caller.
 */
function isIgnoringTheChoice(selection: VoiceSelection): boolean {
  return (
    selection.persona_voice_id !== null &&
    selection.persona_voice_id !== selection.resolved_voice_id
  );
}

/**
 * What to call a voice.
 *
 * A resolved voice the catalogue does not list is one the provider no longer offers, or a
 * cloned one that was never in the list. Its id is then the only handle anybody has on it, and
 * showing the id beats showing nothing to somebody about to ask why their calls sound wrong.
 */
function nameOf(catalogue: VoiceCatalogue, voiceId: string): string {
  return catalogue.voices.find(voice => voice.id === voiceId)?.name ?? voiceId;
}
