import React from 'react';
import { Pressable, StyleSheet, Text } from 'react-native';

import { theme } from '../theme';
import { Icon } from './icon/Icon';

interface Props {
  readonly label: string;
  /** Present when the tag can be taken away. */
  readonly onRemove?: () => void;
  readonly removeLabel?: string;
  readonly selected?: boolean;
  readonly testID?: string;
}

/** A short word in a pill: a topic, a fact the assistant may share. */
export function Tag({
  label,
  onRemove,
  removeLabel,
  selected = true,
  testID,
}: Props): React.JSX.Element {
  const body = (
    <>
      <Text style={[styles.text, selected && styles.selectedText]}>
        {label}
      </Text>
      {onRemove !== undefined && (
        <Icon
          name="x"
          size={14}
          colour={selected ? theme.colour.onAccent : theme.colour.textMuted}
        />
      )}
    </>
  );

  if (onRemove === undefined) {
    return (
      <Text testID={testID} style={[styles.tag, selected && styles.selected]}>
        {label}
      </Text>
    );
  }

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={removeLabel ?? label}
      onPress={onRemove}
      hitSlop={6}
      testID={testID}
      style={[styles.tag, selected && styles.selected]}
    >
      {body}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  tag: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    minHeight: 36,
    paddingHorizontal: 14,
    borderRadius: theme.radius.pill,
    borderWidth: 1,
    borderColor: theme.colour.border,
    backgroundColor: theme.colour.surface,
    ...theme.type.subtitle,
    color: theme.colour.textMuted,
    overflow: 'hidden',
  },
  selected: {
    backgroundColor: theme.colour.accent,
    borderColor: theme.colour.accent,
  },
  text: { ...theme.type.subtitle, color: theme.colour.textMuted },
  selectedText: { color: theme.colour.onAccent },
});
