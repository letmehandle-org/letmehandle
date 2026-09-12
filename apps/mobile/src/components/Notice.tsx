import React from 'react';
import { StyleSheet, Text } from 'react-native';

import { theme } from '../theme';

interface Props {
  readonly message: string;
  readonly tone?: 'problem' | 'quiet';
  readonly testID?: string;
}

/**
 * Something the screen needs to say, announced rather than only drawn.
 *
 * A live region, because these appear after the screen has settled — a save failed, a save
 * worked — and a message that only changes the pixels is one a screen reader never mentions.
 * A problem is assertive and a confirmation is polite: being interrupted matters for the first
 * and is rude for the second.
 */
export function Notice({
  message,
  tone = 'quiet',
  testID,
}: Props): React.JSX.Element {
  return (
    <Text
      accessibilityLiveRegion={tone === 'problem' ? 'assertive' : 'polite'}
      accessibilityRole={tone === 'problem' ? 'alert' : 'text'}
      testID={testID}
      style={[styles.base, tone === 'problem' && styles.problem]}
    >
      {message}
    </Text>
  );
}

const styles = StyleSheet.create({
  base: { ...theme.type.body, fontSize: 14, color: theme.colour.textMuted },
  problem: { color: theme.colour.warning },
});
