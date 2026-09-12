import React from 'react';
import { useTranslation } from 'react-i18next';
import { StyleSheet, Text, View } from 'react-native';

import { Dial } from '../components/Dial';
import { Icon } from '../components/icon/Icon';
import { Screen } from '../components/Screen';
import { theme } from '../theme';

/**
 * Activity before there is any.
 *
 * Call history is phase 11. An empty list is still a designed state: one mark and one line, not
 * a blank screen that looks broken.
 */
export function ActivityScreen(): React.JSX.Element {
  const { t } = useTranslation();
  return (
    <Screen title={t('activity.title')} insideTabs testID="activity-screen">
      <View style={styles.empty}>
        <Dial size={96} blank>
          <Icon name="activity" colour={theme.colour.textGhost} size={28} />
        </Dial>
        <Text style={styles.text}>{t('activity.empty')}</Text>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  empty: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: theme.space.md,
    paddingBottom: theme.space.xl,
  },
  text: {
    ...theme.type.body,
    color: theme.colour.textMuted,
    textAlign: 'center',
  },
});
