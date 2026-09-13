import React from 'react';
import { StyleSheet, View } from 'react-native';

import { theme } from '../theme';

interface Props {
  readonly current: number;
  readonly total: number;
  readonly label: string;
}

/** How far through setup somebody is, as a row of short bars. */
export function Steps({ current, total, label }: Props): React.JSX.Element {
  return (
    <View
      accessibilityRole="progressbar"
      accessibilityLabel={label}
      accessibilityValue={{ min: 1, max: total, now: current }}
      style={styles.row}
      testID="steps"
    >
      {Array.from({ length: total }, (_, index) => (
        <View
          key={index}
          style={[
            styles.bar,
            index < current - 1 && styles.done,
            index === current - 1 && styles.now,
          ]}
        />
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    gap: theme.space.xs,
    paddingTop: theme.space.sm,
  },
  bar: {
    flex: 1,
    height: 4,
    borderRadius: 2,
    backgroundColor: theme.colour.border,
  },
  done: { backgroundColor: theme.colour.accentSpark },
  now: { backgroundColor: theme.colour.accent },
});
