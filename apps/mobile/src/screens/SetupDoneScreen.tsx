import React from 'react';
import { useTranslation } from 'react-i18next';
import { StyleSheet, Text, View } from 'react-native';

import { Button } from '../components/Button';
import { Dial } from '../components/Dial';
import { Icon } from '../components/icon/Icon';
import { Screen } from '../components/Screen';
import { theme } from '../theme';
import { useVoiceSettings } from '../voice/useVoiceSettings';

interface Props {
  readonly onDone: () => void;
}

/**
 * The end of setup.
 *
 * A voice is already chosen, so nothing more is asked; the chip says which one, so somebody who
 * cares knows it exists and can change it later in Personalise.
 */
export function SetupDoneScreen({ onDone }: Props): React.JSX.Element {
  const { t } = useTranslation();
  const { state } = useVoiceSettings();
  const voice =
    state.status === 'ready'
      ? state.catalogue.voices.find(
          entry => entry.id === state.selection.resolved_voice_id,
        )?.name
      : undefined;

  return (
    <Screen
      testID="setup-done"
      footer={
        <Button
          label={t('setup.done.home')}
          onPress={onDone}
          testID="setup-done-home"
        />
      }
    >
      <View style={styles.centre}>
        <Dial size={200} blank>
          <Icon name="check" colour={theme.colour.accent} size={48} />
        </Dial>
        <Text accessibilityRole="header" style={styles.title}>
          {t('setup.done.title')}
        </Text>
        {voice !== undefined && (
          <View style={styles.chip} testID="setup-done-voice">
            <Icon name="speaker" colour={theme.colour.accentDeep} size={18} />
            <Text style={styles.chipText}>
              {t('setup.done.voice', { voice })}
            </Text>
          </View>
        )}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  centre: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: theme.space.lg,
  },
  title: { ...theme.type.title, color: theme.colour.text, textAlign: 'center' },
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: theme.space.sm,
    paddingVertical: 10,
    paddingHorizontal: 16,
    borderRadius: theme.radius.pill,
    backgroundColor: theme.colour.surface,
    ...theme.shadow.card,
  },
  chipText: { ...theme.type.subtitle, color: theme.colour.text },
});
