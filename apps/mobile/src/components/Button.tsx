import React from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text } from 'react-native';

import { theme } from '../theme';

interface Props {
  readonly label: string;
  readonly onPress: () => void;
  readonly disabled?: boolean;
  readonly busy?: boolean;
  readonly variant?: 'primary' | 'quiet';
  readonly testID?: string;
}

/**
 * The one button.
 *
 * `busy` is separate from `disabled` because they mean different things to somebody looking at
 * the screen: one is "wait", the other is "you cannot do this yet". A single flag would make
 * every slow action look like a broken one.
 */
export function Button({
  label,
  onPress,
  disabled = false,
  busy = false,
  variant = 'primary',
  testID,
}: Props): React.JSX.Element {
  const inactive = disabled || busy;

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled: inactive, busy }}
      accessibilityLabel={label}
      testID={testID}
      disabled={inactive}
      onPress={onPress}
      style={({ pressed }) => [
        styles.base,
        variant === 'primary' ? styles.primary : styles.quiet,
        inactive && styles.inactive,
        pressed && !inactive && styles.pressed,
      ]}
    >
      {busy ? (
        <ActivityIndicator color={theme.colour.background} />
      ) : (
        <Text style={[styles.label, variant === 'quiet' && styles.quietLabel]}>
          {label}
        </Text>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  base: {
    minHeight: 52,
    borderRadius: theme.radius.md,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: theme.space.lg,
  },
  primary: { backgroundColor: theme.colour.accent },
  quiet: { backgroundColor: 'transparent' },
  inactive: { opacity: 0.4 },
  pressed: { opacity: 0.85 },
  label: {
    ...theme.type.body,
    fontWeight: '600',
    color: theme.colour.background,
  },
  quietLabel: { color: theme.colour.textMuted },
});
