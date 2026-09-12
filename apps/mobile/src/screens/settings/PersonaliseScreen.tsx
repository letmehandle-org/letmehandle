import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator, StyleSheet, Text } from 'react-native';

import type { Formality, Verbosity } from '@letmehandle/api-client';

import { describeFailure } from '../../api/messages';
import { Button } from '../../components/Button';
import { Card } from '../../components/Card';
import {
  ChoiceCards,
  SpeechLines,
  type CardOption,
} from '../../components/ChoiceCards';
import { Icon, type IconName } from '../../components/icon/Icon';
import { Notice } from '../../components/Notice';
import { Row } from '../../components/Row';
import { Screen } from '../../components/Screen';
import { VoicePicker } from '../../components/VoicePicker';
import { usePreferences } from '../../preferences/PreferencesProvider';
import { FORMALITIES, VERBOSITIES } from '../../preferences/options';
import { personalityWith } from '../../preferences/rules';
import { useImmediateSave } from '../../preferences/useImmediateSave';
import { theme } from '../../theme';
import { useVoiceSettings } from '../../voice/useVoiceSettings';

interface Props {
  readonly onBack: () => void;
  readonly onOpenTopics: () => void;
}

const MANNER_ICONS: Record<Formality, IconName> = {
  warm: 'sun',
  neutral: 'msg',
  formal: 'shield',
};
const LENGTH_LINES: Record<Verbosity, readonly number[]> = {
  brief: [60],
  normal: [100, 55],
  detailed: [100, 100, 70],
};

/**
 * How the assistant comes across. Optional, and never part of setup.
 *
 * A voice is already chosen for everybody; everything here is a tap on a picture rather than a
 * word — voices as faces, manner as icons, length drawn as lines of speech.
 */
export function PersonaliseScreen({
  onBack,
  onOpenTopics,
}: Props): React.JSX.Element {
  const { t } = useTranslation();
  const { preferences } = usePreferences();
  const { problem, save } = useImmediateSave();
  const personality = preferences.personality;

  const manner: CardOption<Formality>[] = FORMALITIES.map(value => ({
    value,
    label: t(`preferences.formality.${value}`),
    renderGlyph: selected => (
      <Icon
        name={MANNER_ICONS[value]}
        size={26}
        colour={selected ? theme.colour.accentDeep : theme.colour.textFaint}
      />
    ),
  }));
  const length: CardOption<Verbosity>[] = VERBOSITIES.map(value => ({
    value,
    label: t(`preferences.verbosity.${value}`),
    renderGlyph: selected => (
      <SpeechLines lines={LENGTH_LINES[value]} selected={selected} />
    ),
  }));

  return (
    <Screen
      onBack={onBack}
      title={t('personalise.title')}
      scrollable
      testID="settings-personalise"
    >
      <Text style={styles.label}>{t('personalise.voice')}</Text>
      <Voices />

      <Text style={styles.label}>{t('personalise.manner')}</Text>
      <ChoiceCards
        label={t('personalise.manner')}
        value={personality.formality}
        options={manner}
        onChange={formality => {
          save({ personality: personalityWith(preferences, { formality }) });
        }}
        testID="personality-formality"
      />

      <Text style={styles.label}>{t('personalise.length')}</Text>
      <ChoiceCards
        label={t('personalise.length')}
        value={personality.verbosity}
        options={length}
        onChange={verbosity => {
          save({ personality: personalityWith(preferences, { verbosity }) });
        }}
        testID="personality-verbosity"
      />

      <Card style={styles.topics}>
        <Row
          icon="tag"
          tone="assistant"
          title={t('personalise.topics')}
          value={String((personality.topics ?? []).length)}
          onPress={onOpenTopics}
          last
          testID="personalise-open-topics"
        />
      </Card>

      {problem !== null && (
        <Notice tone="problem" message={problem} testID="settings-problem" />
      )}
    </Screen>
  );
}

function Voices(): React.JSX.Element {
  const { t } = useTranslation();
  const { state, reload, choose } = useVoiceSettings();
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (state.status === 'loading') {
    return (
      <ActivityIndicator color={theme.colour.accent} testID="voice-loading" />
    );
  }
  if (state.status === 'unavailable') {
    return (
      <>
        <Notice
          tone="problem"
          message={t('personalise.voiceUnavailable')}
          testID="voice-problem"
        />
        <Button
          label={t('common.tryAgain')}
          variant="ghost"
          onPress={reload}
          testID="voice-retry"
        />
      </>
    );
  }

  const { catalogue, selection } = state;
  const chosen = selection.persona_voice_id;
  // The voice callers actually hear is the one drawn as chosen, so the picture never disagrees
  // with the call.
  const shown = selection.resolved_voice_id;
  const notYours =
    chosen !== null && selection.cloned_voice_id === null && chosen !== shown;
  const shownName =
    catalogue.voices.find(voice => voice.id === shown)?.name ?? shown;

  return (
    <>
      <VoicePicker
        label={t('personalise.voice')}
        voices={catalogue.voices}
        selected={shown}
        disabled={busy}
        onChoose={id => {
          setBusy(true);
          setProblem(null);
          choose(id)
            .catch((error: unknown) => {
              setProblem(
                describeFailure(error, t, {
                  refused: t('personalise.voiceSaveFailed'),
                }),
              );
            })
            .finally(() => {
              setBusy(false);
            });
        }}
      />
      {notYours && (
        <Notice
          message={t('personalise.voiceNotYourChoice', { voice: shownName })}
          testID="voice-not-yours"
        />
      )}
      {problem !== null && (
        <Notice tone="problem" message={problem} testID="voice-problem" />
      )}
    </>
  );
}

const styles = StyleSheet.create({
  label: {
    ...theme.type.label,
    color: theme.colour.textFaint,
    marginTop: theme.space.sm,
    marginLeft: theme.space.sm,
  },
  topics: { marginTop: theme.space.sm },
});
