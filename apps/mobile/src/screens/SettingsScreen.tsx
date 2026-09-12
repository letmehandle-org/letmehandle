import React from 'react';
import { useTranslation } from 'react-i18next';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { Screen } from '../components/Screen';
import {
  PREFERENCE_SECTIONS,
  type PreferenceSection,
} from '../preferences/options';
import { theme } from '../theme';

interface Props {
  readonly onOpenSection: (section: PreferenceSection) => void;
  readonly onOpenVoice: () => void;
}

/**
 * The way back into every answer given during setup.
 *
 * Every section is here, including the ones onboarding let somebody skip. Nothing about this
 * product is set once: a preference that could only be given during setup would be one people
 * reinstall the application to change.
 *
 * The voice sits alongside them although it was never a setup question. Setup asks what the
 * server says is left to ask, and the server has no voice step — which is a statement about
 * what somebody must answer before their assistant can work, not about where they should later
 * look for it.
 */
export function SettingsScreen({
  onOpenSection,
  onOpenVoice,
}: Props): React.JSX.Element {
  const { t } = useTranslation();

  return (
    <Screen
      title={t('settings.title')}
      subtitle={t('settings.subtitle')}
      scrollable
      testID="settings-screen"
    >
      {PREFERENCE_SECTIONS.map(section => (
        <Row
          key={section}
          title={t(`preferences.${section}.title`)}
          subtitle={t(`preferences.${section}.subtitle`)}
          testID={`settings-open-${section}`}
          onPress={() => {
            onOpenSection(section);
          }}
        />
      ))}

      <Row
        title={t('voice.title')}
        subtitle={t('voice.subtitle')}
        testID="settings-open-voice"
        onPress={onOpenVoice}
      />
    </Screen>
  );
}

function Row({
  title,
  subtitle,
  testID,
  onPress,
}: {
  readonly title: string;
  readonly subtitle: string;
  readonly testID: string;
  readonly onPress: () => void;
}): React.JSX.Element {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={title}
      testID={testID}
      onPress={onPress}
      style={({ pressed }) => [styles.row, pressed && styles.pressed]}
    >
      <View style={styles.rowText}>
        <Text style={styles.title}>{title}</Text>
        <Text style={styles.subtitle}>{subtitle}</Text>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: {
    backgroundColor: theme.colour.surface,
    borderRadius: theme.radius.sm,
    padding: theme.space.md,
    minHeight: 64,
    justifyContent: 'center',
  },
  pressed: { opacity: 0.85 },
  rowText: { gap: theme.space.xs },
  title: { ...theme.type.body, color: theme.colour.text },
  subtitle: {
    ...theme.type.body,
    fontSize: 14,
    color: theme.colour.textMuted,
  },
});
