import React from 'react';
import { StyleSheet, View, type StyleProp, type ViewStyle } from 'react-native';

import { theme } from '../theme';

interface Props {
  readonly children: React.ReactNode;
  readonly style?: StyleProp<ViewStyle>;
  readonly testID?: string;
}

/**
 * White on the lavender ground: everything a person reads sits on one of these.
 *
 * The only elevated surface in the app. Rows inside it are separated by hairlines rather than
 * by more cards, so the screen keeps one level of depth instead of stacking shadows.
 */
export function Card({ children, style, testID }: Props): React.JSX.Element {
  return (
    <View style={[styles.card, style]} testID={testID}>
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: theme.colour.surface,
    borderRadius: theme.radius.md,
    paddingHorizontal: theme.space.md,
    ...theme.shadow.card,
  },
});
