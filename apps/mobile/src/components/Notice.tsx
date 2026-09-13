import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { theme } from '../theme';
import { Icon, type IconName } from './icon/Icon';

type Tone = 'quiet' | 'problem' | 'done';

interface Props {
  readonly message: string;
  readonly tone?: Tone;
  readonly testID?: string;
}

const LOOK: Record<
  Tone,
  { icon: IconName; ink: string; fill: string; text: string }
> = {
  quiet: {
    icon: 'info',
    ink: theme.colour.textMuted,
    fill: theme.colour.surface,
    text: theme.colour.textMuted,
  },
  problem: {
    icon: 'alert',
    ink: theme.colour.warning,
    fill: theme.colour.warningWash,
    text: theme.colour.warning,
  },
  done: {
    icon: 'check-c',
    ink: theme.colour.accentDeep,
    fill: theme.colour.accentWash,
    text: theme.colour.accentDeep,
  },
};

/** A message announced as a live region: assertive for a problem, polite for a confirmation. */
export function Notice({
  message,
  tone = 'quiet',
  testID,
}: Props): React.JSX.Element {
  const look = LOOK[tone];
  return (
    <View style={[styles.banner, { backgroundColor: look.fill }]}>
      <Icon name={look.icon} colour={look.ink} size={18} />
      <Text
        accessibilityLiveRegion={tone === 'problem' ? 'assertive' : 'polite'}
        accessibilityRole={tone === 'problem' ? 'alert' : 'text'}
        testID={testID}
        style={[styles.text, { color: look.text }]}
      >
        {message}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  banner: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: theme.space.sm,
    paddingHorizontal: theme.space.md,
    paddingVertical: theme.space.row,
    borderRadius: theme.radius.md,
  },
  text: { ...theme.type.caption, flex: 1 },
});
