import React from 'react';
import { StyleSheet, Switch, Text, View } from 'react-native';

import { theme } from '../theme';

interface Props {
  readonly label: string;
  readonly value: boolean;
  readonly onChange: (value: boolean) => void;
  readonly testID?: string;
}

/**
 * A single yes or no.
 *
 * The label is given to the switch itself rather than sitting beside it as decoration, because
 * a switch announced as "off" with no idea what is off is a control nobody can use without
 * sight.
 */
export function Toggle({
  label,
  value,
  onChange,
  testID,
}: Props): React.JSX.Element {
  return (
    <View style={styles.row}>
      <Text style={styles.label}>{label}</Text>
      <Switch
        accessibilityRole="switch"
        accessibilityLabel={label}
        accessibilityState={{ checked: value }}
        testID={testID}
        value={value}
        onValueChange={onChange}
        trackColor={{
          true: theme.colour.accent,
          false: theme.colour.border,
        }}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: theme.space.md,
    minHeight: 48,
  },
  label: { ...theme.type.body, color: theme.colour.text, flexShrink: 1 },
});
