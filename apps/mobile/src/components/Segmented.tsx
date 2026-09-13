import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { theme } from '../theme';

export interface SegmentOption<T extends string> {
  readonly value: T;
  readonly label: string;
}

interface Props<T extends string> {
  readonly label: string;
  readonly value: T;
  readonly options: readonly SegmentOption<T>[];
  readonly onChange: (value: T) => void;
  readonly testID?: string;
}

/**
 * A choice between a few things that are each a whole answer, like "all the time" or "set hours".
 *
 * The selected option is mounted as a raised pill rather than restyled, for the reason the tab
 * bar's is: Android drops the corner radius of a view whose background arrives after it drew.
 */
export function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
  testID,
}: Props<T>): React.JSX.Element {
  return (
    <View
      accessibilityRole="radiogroup"
      accessibilityLabel={label}
      style={styles.track}
      testID={testID}
    >
      {options.map(option => {
        const selected = option.value === value;
        return (
          <Pressable
            key={option.value}
            accessibilityRole="radio"
            accessibilityState={{ checked: selected }}
            accessibilityLabel={option.label}
            onPress={() => {
              if (!selected) {
                onChange(option.value);
              }
            }}
            style={styles.option}
            testID={
              testID === undefined ? undefined : `${testID}-${option.value}`
            }
          >
            {selected && <View style={styles.pill} />}
            <Text style={[styles.text, selected && styles.textOn]}>
              {option.label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  track: {
    flexDirection: 'row',
    padding: theme.space.xs,
    borderRadius: theme.radius.pill,
    backgroundColor: theme.colour.well,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: theme.colour.border,
  },
  option: {
    flex: 1,
    minHeight: theme.touch.min,
    alignItems: 'center',
    justifyContent: 'center',
  },
  pill: {
    ...StyleSheet.absoluteFill,
    borderRadius: theme.radius.pill,
    backgroundColor: theme.colour.surface,
    ...theme.shadow.card,
  },
  text: { ...theme.type.subtitle, color: theme.colour.textMuted },
  textOn: { fontFamily: theme.font.strong, color: theme.colour.accentDeep },
});
