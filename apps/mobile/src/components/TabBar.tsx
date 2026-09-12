import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { theme } from '../theme';
import { Icon, type IconName } from './icon/Icon';

export interface Tab<K extends string> {
  readonly key: K;
  readonly label: string;
  readonly icon: IconName;
}

interface Props<K extends string> {
  readonly tabs: readonly Tab<K>[];
  readonly current: K;
  readonly onSelect: (key: K) => void;
}

/**
 * The three places in the app: Home, Activity, Settings.
 *
 * The current tab sits in a soft violet pill as well as being coloured, so which one is open
 * does not depend on telling two colours apart.
 */
export function TabBar<K extends string>({
  tabs,
  current,
  onSelect,
}: Props<K>): React.JSX.Element {
  const { bottom } = useSafeAreaInsets();
  return (
    <View
      accessibilityRole="tablist"
      style={[styles.bar, { paddingBottom: Math.max(bottom, theme.space.sm) }]}
    >
      {tabs.map(tab => {
        const selected = tab.key === current;
        const ink = selected ? theme.colour.accentDeep : theme.colour.textFaint;
        return (
          <Pressable
            key={tab.key}
            accessibilityRole="tab"
            accessibilityLabel={tab.label}
            accessibilityState={{ selected }}
            testID={`tab-${tab.key}`}
            onPress={() => {
              onSelect(tab.key);
            }}
            style={styles.tab}
          >
            <View style={[styles.pill, selected && styles.pillOn]}>
              <Icon name={tab.icon} colour={ink} size={22} />
            </View>
            <Text
              style={[styles.label, { color: ink }, selected && styles.labelOn]}
            >
              {tab.label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  bar: {
    flexDirection: 'row',
    backgroundColor: theme.colour.surface,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: theme.colour.border,
    paddingTop: theme.space.sm,
  },
  tab: { flex: 1, alignItems: 'center', gap: 2, minHeight: theme.touch.min },
  pill: {
    paddingHorizontal: 18,
    paddingVertical: 4,
    borderRadius: theme.radius.pill,
  },
  pillOn: { backgroundColor: theme.colour.accentWash },
  label: { ...theme.type.caption, fontSize: 12 },
  labelOn: { fontFamily: theme.font.strong },
});
