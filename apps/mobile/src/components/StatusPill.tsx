import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { theme } from '../theme';

interface Props {
  readonly label: string;
  readonly tone?: 'assistant' | 'needsYou' | 'quiet';
  readonly testID?: string;
}

const DOT = {
  assistant: theme.colour.accent,
  needsYou: theme.colour.needsYouDeep,
  quiet: theme.colour.textGhost,
} as const;

/** A dot and a word: what state the assistant is in, at the top of Home. */
export function StatusPill({
  label,
  tone = 'quiet',
  testID,
}: Props): React.JSX.Element {
  return (
    <View
      style={styles.pill}
      testID={testID}
      accessible
      accessibilityLabel={label}
    >
      <View style={[styles.dot, { backgroundColor: DOT[tone] }]} />
      <Text style={styles.text}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  pill: {
    alignSelf: 'flex-start',
    flexDirection: 'row',
    alignItems: 'center',
    gap: theme.space.sm,
    paddingVertical: 8,
    paddingLeft: 12,
    paddingRight: 16,
    borderRadius: theme.radius.pill,
    backgroundColor: theme.colour.surface,
    ...theme.shadow.card,
  },
  dot: { width: 9, height: 9, borderRadius: 4.5 },
  text: {
    ...theme.type.subtitle,
    fontFamily: theme.font.strong,
    color: theme.colour.text,
  },
});
