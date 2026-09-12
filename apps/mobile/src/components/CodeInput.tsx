import React, { useRef } from 'react';
import { Pressable, StyleSheet, Text, TextInput, View } from 'react-native';

import { theme } from '../theme';

interface Props {
  readonly label: string;
  readonly value: string;
  readonly onChange: (value: string) => void;
  readonly length?: number;
  readonly problem?: boolean;
  readonly testID?: string;
}

/**
 * A code, as a row of boxes.
 *
 * One real text input sits invisibly over the boxes, so the keyboard, paste and the system's
 * one-time-code autofill all work as they do anywhere else; the boxes only draw what it holds.
 */
export function CodeInput({
  label,
  value,
  onChange,
  length = 6,
  problem = false,
  testID,
}: Props): React.JSX.Element {
  const input = useRef<React.ComponentRef<typeof TextInput>>(null);
  const digits = value.split('');

  return (
    <Pressable
      accessible={false}
      onPress={() => {
        input.current?.focus();
      }}
      style={styles.row}
    >
      {Array.from({ length }, (_, index) => {
        const filled = index < digits.length;
        const active =
          index === Math.min(digits.length, length - 1) && !problem;
        return (
          <View
            key={index}
            style={[
              styles.box,
              active && styles.active,
              problem && filled && styles.problem,
            ]}
          >
            <Text style={[styles.digit, problem && styles.problemText]}>
              {digits[index] ?? ''}
            </Text>
          </View>
        );
      })}
      <TextInput
        ref={input}
        accessibilityLabel={label}
        value={value}
        onChangeText={text => {
          onChange(text.replace(/\D/g, '').slice(0, length));
        }}
        keyboardType="number-pad"
        textContentType="oneTimeCode"
        autoComplete="one-time-code"
        maxLength={length}
        autoFocus
        caretHidden
        style={styles.hidden}
        testID={testID}
      />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', justifyContent: 'center', gap: 8 },
  box: {
    width: 48,
    height: 60,
    borderRadius: theme.radius.sm,
    borderWidth: 1.5,
    borderColor: theme.colour.border,
    backgroundColor: theme.colour.surface,
    alignItems: 'center',
    justifyContent: 'center',
    ...theme.shadow.card,
  },
  active: { borderColor: theme.colour.accent },
  problem: { borderColor: theme.colour.warning },
  digit: {
    ...theme.type.heading,
    fontFamily: theme.font.regular,
    color: theme.colour.text,
  },
  problemText: { color: theme.colour.warning },
  hidden: { ...StyleSheet.absoluteFill, opacity: 0.02, color: 'transparent' },
});
