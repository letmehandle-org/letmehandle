import React from 'react';
import {
  StyleSheet,
  Text,
  TextInput,
  View,
  type TextInputProps,
} from 'react-native';

import { theme } from '../theme';

interface Props extends Omit<TextInputProps, 'style'> {
  readonly label: string;
  readonly problem?: string | null;
}

/**
 * A labelled input, with room for what is wrong with it.
 *
 * The problem sits with the field rather than in a banner elsewhere on the screen: a message
 * next to the thing it is about is one people read.
 */
export function Field({
  label,
  problem = null,
  ...input
}: Props): React.JSX.Element {
  return (
    <View style={styles.container}>
      <Text style={styles.label}>{label}</Text>
      <TextInput
        {...input}
        accessibilityLabel={label}
        placeholderTextColor={theme.colour.textMuted}
        style={[styles.input, problem !== null && styles.inputWithProblem]}
      />
      {problem !== null && (
        <Text accessibilityLiveRegion="polite" style={styles.problem}>
          {problem}
        </Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { gap: theme.space.xs },
  label: { ...theme.type.body, fontSize: 14, color: theme.colour.textMuted },
  input: {
    ...theme.type.body,
    color: theme.colour.text,
    backgroundColor: theme.colour.surface,
    borderColor: theme.colour.border,
    borderWidth: 1,
    borderRadius: theme.radius.sm,
    paddingHorizontal: theme.space.md,
    minHeight: 52,
  },
  inputWithProblem: { borderColor: theme.colour.warning },
  problem: { ...theme.type.body, fontSize: 14, color: theme.colour.warning },
});
