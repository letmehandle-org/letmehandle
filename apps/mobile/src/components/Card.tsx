import React from 'react';
import { StyleSheet, View, type StyleProp, type ViewStyle } from 'react-native';

import { theme } from '../theme';

interface Props {
  readonly children: React.ReactNode;
  readonly style?: StyleProp<ViewStyle>;
  readonly testID?: string;
}

/** The app's one elevated surface; rows inside are separated by hairlines. */
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
