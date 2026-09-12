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
 * one, not one behind a flag. There is no play control either: the provider declares it has
 * no samples and the generated client has no route to fetch one with, so a button here could
 * only be wired to a request that 404s. The voice tests hold a compile-time check on the schema
 * that fails the day that route exists, which is when a control belongs here — drawn for the
 * voices marked `previewable`, not for every voice in the list.
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
        message={t(answeringMessage(catalogue, selection), {
          voice: nameOf(catalogue, selection.resolved_voice_id),
        })}
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
 * The voice this user's choices say should be answering.
 *
 * A cloned voice outranks a chosen one, in the same order the server resolves them in. Comparing
 * the answering voice against the chosen one alone would report a working clone as a problem.
 */
function expectedVoice(selection: VoiceSelection): string | null {
  return selection.cloned_voice_id ?? selection.persona_voice_id;
}

/**
 * Whether the voice answering calls is not the one that was asked for.
 *
 * These differ only when an expected voice has become unavailable and the fallback chain has
 * quietly stepped past it. Somebody who picked a voice deserves to be told that, rather than
 * finding out from a caller.
 */
function isIgnoringTheChoice(selection: VoiceSelection): boolean {
  const expected = expectedVoice(selection);
  return expected !== null && expected !== selection.resolved_voice_id;
}

/**
 * What to tell somebody about the voice their calls are answered in.
 *
 * A chosen voice missing from the catalogue has been withdrawn, and that can be said plainly.
 * Anything else stepped past is only known to be unavailable, so that is all that is said. A
 * cloned voice is never in the catalogue, so its absence from it proves nothing.
 */
function answeringMessage(
  catalogue: VoiceCatalogue,
  selection: VoiceSelection,
): 'voice.inUse' | 'voice.notYourChoice' | 'voice.withdrawn' {
  if (!isIgnoringTheChoice(selection)) {
    return 'voice.inUse';
  }
  const chosen = selection.persona_voice_id;
  const withdrawn =
    selection.cloned_voice_id === null &&
    chosen !== null &&
    !catalogue.voices.some(voice => voice.id === chosen);
  return withdrawn ? 'voice.withdrawn' : 'voice.notYourChoice';
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
