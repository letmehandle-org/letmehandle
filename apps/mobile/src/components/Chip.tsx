import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { theme } from '../theme';
import { Icon, type IconName } from './icon/Icon';

interface Props {
  readonly label: string;
  /** Present when the chip is one choice among several. */
  readonly onPress?: () => void;
  readonly selected?: boolean;
  /** A soft violet chip that states something, such as how a call ended. */
  readonly emphasis?: boolean;
  readonly icon?: IconName;
  readonly testID?: string;
}

/**
 * A short word in a pill: a filter to choose, or a fact about a call.
 *
 * A chip that can be chosen is a radio, so a screen reader says which is on; one that only
 * states something is text.
 */
export function Chip({
  label,
  onPress,
  selected = false,
  emphasis = false,
  icon,
  testID,
}: Props): React.JSX.Element {
  const content = (
    <>
      {icon !== undefined && (
        <Icon
          name={icon}
          size={14}
          colour={selected ? theme.colour.onAccent : theme.colour.accentDeep}
        />
      )}
      <Text
        style={[
          styles.text,
          emphasis && styles.emphasisText,
          selected && styles.selectedText,
        ]}
      >
        {label}
      </Text>
    </>
  );
  const look = [
    styles.chip,
    emphasis && styles.emphasis,
    selected && styles.selected,
  ];

  if (onPress === undefined) {
    return (
      <View style={look} testID={testID}>
        {content}
      </View>
    );
  }
  return (
    <Pressable
      accessibilityRole="radio"
      accessibilityState={{ checked: selected }}
      accessibilityLabel={label}
      onPress={onPress}
      hitSlop={4}
      style={({ pressed }) => [...look, pressed && styles.pressed]}
      testID={testID}
    >
      {content}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    minHeight: 40,
    paddingHorizontal: 16,
    borderRadius: theme.radius.pill,
    borderWidth: 1,
    borderColor: theme.colour.border,
    backgroundColor: theme.colour.surface,
  },
  emphasis: {
    backgroundColor: theme.colour.accentWash,
    borderColor: theme.colour.accentWash,
  },
  selected: {
    backgroundColor: theme.colour.accent,
    borderColor: theme.colour.accent,
  },
  pressed: { opacity: 0.8 },
  text: { ...theme.type.subtitle, color: theme.colour.textMuted },
  emphasisText: { color: theme.colour.accentDeep },
  selectedText: { fontFamily: theme.font.strong, color: theme.colour.onAccent },
});
