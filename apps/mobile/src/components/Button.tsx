import React from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text } from 'react-native';

import { theme } from '../theme';
import { Icon, type IconName } from './icon/Icon';

type Variant = 'primary' | 'ghost' | 'quiet' | 'danger';

interface Props {
  readonly label: string;
  readonly onPress: () => void;
  readonly disabled?: boolean;
  readonly busy?: boolean;
  readonly variant?: Variant;
  readonly icon?: IconName;
  readonly testID?: string;
}

const INK: Record<Variant, string> = {
  primary: theme.colour.onAccent,
  ghost: theme.colour.text,
  quiet: theme.colour.textMuted,
  danger: theme.colour.warning,
};

/**
 * The one button, in four voices.
 *
 * Primary is the single thing a screen wants done; ghost is a real alternative; quiet is a way
 * out; danger destroys something and is never violet, so deleting never looks like the brand.
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
  icon,
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
        styles[variant],
        inactive && styles.inactive,
        pressed && !inactive && styles.pressed,
      ]}
    >
      {busy ? (
        <ActivityIndicator color={INK[variant]} />
      ) : (
        <>
          {icon !== undefined && (
            <Icon name={icon} colour={INK[variant]} size={20} />
          )}
          <Text style={[styles.label, { color: INK[variant] }]}>{label}</Text>
        </>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  base: {
    minHeight: 56,
    borderRadius: theme.radius.pill,
    alignItems: 'center',
    justifyContent: 'center',
    flexDirection: 'row',
    gap: theme.space.sm,
    paddingHorizontal: theme.space.lg,
  },
  primary: { backgroundColor: theme.colour.accent, ...theme.shadow.accent },
  ghost: {
    backgroundColor: theme.colour.surface,
    borderWidth: 1,
    borderColor: theme.colour.border,
    ...theme.shadow.card,
  },
  quiet: { backgroundColor: 'transparent', minHeight: theme.touch.min },
  danger: { backgroundColor: theme.colour.warningWash },
  inactive: { opacity: 0.45 },
  pressed: { opacity: 0.85 },
  label: { ...theme.type.button },
});
