import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { useTranslation } from 'react-i18next';

import { theme } from '../theme';

/**
 * The application shell.
 *
 * Deliberately almost empty. Phase 0 builds the structure; what Home actually communicates is
 * phase 9's work, against a design that does not exist yet.
 */
export function HomeScreen(): React.JSX.Element {
  const { t } = useTranslation();

  return (
    <View style={styles.container} testID="home-screen">
      <Text style={styles.title}>{t('home.title')}</Text>
      <Text style={styles.subtitle}>{t('home.subtitle')}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: theme.colour.background,
    padding: theme.space.lg,
  },
  title: {
    ...theme.type.title,
    color: theme.colour.text,
  },
  subtitle: {
    ...theme.type.body,
    color: theme.colour.textMuted,
    marginTop: theme.space.sm,
    textAlign: 'center',
  },
});
