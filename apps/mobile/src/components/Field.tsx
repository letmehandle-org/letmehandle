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
  /** Something fixed before the input, such as a country code. */
  readonly prefix?: string;
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
  prefix,
  ...input
}: Props): React.JSX.Element {
  return (
    <View style={styles.container}>
      <Text style={styles.label}>{label}</Text>
      <View style={[styles.box, problem !== null && styles.boxWithProblem]}>
        {prefix !== undefined && <Text style={styles.prefix}>{prefix}</Text>}
        <TextInput
          {...input}
          accessibilityLabel={label}
          placeholderTextColor={theme.colour.textGhost}
          style={styles.input}
        />
      </View>
      {problem !== null && (
        <Text accessibilityLiveRegion="polite" style={styles.problem}>
          {problem}
        </Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { gap: theme.space.sm },
  label: { ...theme.type.label, color: theme.colour.textFaint },
  box: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: theme.space.row,
    minHeight: 60,
    paddingHorizontal: theme.space.md,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colour.border,
    backgroundColor: theme.colour.surface,
    ...theme.shadow.card,
  },
  boxWithProblem: { borderColor: theme.colour.warning },
  prefix: { ...theme.type.body, color: theme.colour.textMuted },
  input: {
    ...theme.type.body,
    flex: 1,
    color: theme.colour.text,
    paddingVertical: 0,
  },
  problem: { ...theme.type.caption, color: theme.colour.warning },
});
