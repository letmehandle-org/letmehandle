import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { theme } from '../theme';

export interface Option<T> {
  readonly value: T;
  readonly label: string;
}

interface Props<T> {
  readonly label: string;
  readonly value: T;
  readonly options: readonly Option<T>[];
  readonly onChange: (value: T) => void;
  readonly testID?: string;
}

/**
 * One answer out of a few, all of them visible.
 *
 * A list rather than a picker or a dropdown. These are questions about what an assistant will
 * do on somebody's behalf, and an answer hidden behind a tap is one people accept without
 * having read the alternatives.
 *
 * `radio` rather than `button`, so that assistive technology announces how many choices there
 * are and which one is taken — which a row of buttons does not.
 */
export function Choice<T extends string | number>({
  label,
  value,
  options,
  onChange,
  testID,
}: Props<T>): React.JSX.Element {
  return (
    <View accessibilityRole="radiogroup" style={styles.group}>
      <Text style={styles.label}>{label}</Text>
      {options.map(option => {
        const selected = option.value === value;
        return (
          <Pressable
            key={String(option.value)}
            accessibilityRole="radio"
            accessibilityLabel={option.label}
            accessibilityState={{ selected, checked: selected }}
            testID={
              testID === undefined
                ? undefined
                : `${testID}-${String(option.value)}`
            }
            onPress={() => {
              onChange(option.value);
            }}
            style={({ pressed }) => [
              styles.option,
              selected && styles.selected,
              pressed && styles.pressed,
            ]}
          >
            <Text style={[styles.text, selected && styles.selectedText]}>
              {option.label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  group: { gap: theme.space.xs },
  label: { ...theme.type.body, fontSize: 14, color: theme.colour.textMuted },
  option: {
    minHeight: 48,
    justifyContent: 'center',
    paddingHorizontal: theme.space.md,
    borderRadius: theme.radius.sm,
    borderWidth: 1,
    borderColor: theme.colour.border,
    backgroundColor: theme.colour.surface,
  },
  selected: { borderColor: theme.colour.accent },
  pressed: { opacity: 0.85 },
  text: { ...theme.type.body, color: theme.colour.textMuted },
  selectedText: { color: theme.colour.text, fontWeight: '600' },
});
