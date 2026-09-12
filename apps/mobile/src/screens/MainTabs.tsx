import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { StyleSheet, View } from 'react-native';

import { TabBar, type Tab } from '../components/TabBar';
import { theme } from '../theme';
import { ActivityScreen } from './ActivityScreen';
import { HomeScreen } from './HomeScreen';
import { SettingsScreen, type SettingsPage } from './SettingsScreen';

type TabKey = 'home' | 'activity' | 'settings';

interface Props {
  readonly onOpenSetting: (page: SettingsPage) => void;
}

/**
 * The three places in the app.
 *
 * Tabs held in state rather than as a navigator: three screens with no history of their own do
 * not need one, and settings' own pages open over the tabs on the app stack, where back goes
 * where people expect.
 */
export function MainTabs({ onOpenSetting }: Props): React.JSX.Element {
  const { t } = useTranslation();
  const [current, setCurrent] = useState<TabKey>('home');

  const tabs: readonly Tab<TabKey>[] = [
    { key: 'home', label: t('tabs.home'), icon: 'home' },
    { key: 'activity', label: t('tabs.activity'), icon: 'activity' },
    { key: 'settings', label: t('tabs.settings'), icon: 'settings' },
  ];

  return (
    <View style={styles.fill}>
      <View style={styles.fill}>
        {current === 'home' && <HomeScreen />}
        {current === 'activity' && <ActivityScreen />}
        {current === 'settings' && <SettingsScreen onOpen={onOpenSetting} />}
      </View>
      <TabBar tabs={tabs} current={current} onSelect={setCurrent} />
    </View>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1, backgroundColor: theme.colour.background },
});
