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
}

/**
 * The way back into every answer given during setup.
 *
 * Every section is here, including the ones onboarding let somebody skip. Nothing about this
 * product is set once: a preference that could only be given during setup would be one people
 * reinstall the application to change.
 */
export function SettingsScreen({ onOpenSection }: Props): React.JSX.Element {
  const { t } = useTranslation();

  return (
    <Screen
      title={t('settings.title')}
      subtitle={t('settings.subtitle')}
      scrollable
      testID="settings-screen"
    >
      {PREFERENCE_SECTIONS.map(section => (
        <Pressable
          key={section}
          accessibilityRole="button"
          accessibilityLabel={t(`preferences.${section}.title`)}
          testID={`settings-open-${section}`}
          onPress={() => {
            onOpenSection(section);
          }}
          style={({ pressed }) => [styles.row, pressed && styles.pressed]}
        >
          <View style={styles.rowText}>
            <Text style={styles.title}>
              {t(`preferences.${section}.title`)}
            </Text>
            <Text style={styles.subtitle}>
              {t(`preferences.${section}.subtitle`)}
            </Text>
          </View>
        </Pressable>
      ))}
    </Screen>
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
