import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { theme } from '../theme';

export interface CardOption<T> {
  readonly value: T;
  readonly label: string;
  /** What the card shows above its label: an icon, or a small drawing of the answer. */
  readonly renderGlyph: (selected: boolean) => React.ReactNode;
}

interface Props<T> {
  readonly label: string;
  readonly value: T;
  readonly options: readonly CardOption<T>[];
  readonly onChange: (value: T) => void;
  readonly testID?: string;
}

/**
 * One answer out of a few, each shown as a picture.
 *
 * For choices a word describes badly — how warm, how long — so the answer is seen rather than
 * read. Radios, so assistive technology announces the count and the choice.
 */
export function ChoiceCards<T extends string>({
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
      style={styles.row}
    >
      {options.map(option => {
        const selected = option.value === value;
        return (
          <Pressable
            key={option.value}
            accessibilityRole="radio"
            accessibilityLabel={option.label}
            accessibilityState={{ selected, checked: selected }}
            testID={
              testID === undefined ? undefined : `${testID}-${option.value}`
            }
            onPress={() => {
              onChange(option.value);
            }}
            style={({ pressed }) => [
              styles.card,
              selected && styles.selected,
              pressed && styles.pressed,
            ]}
          >
            {option.renderGlyph(selected)}
            <Text style={[styles.text, selected && styles.selectedText]}>
              {option.label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

/** Length drawn as lines of speech: one short line, two, three. */
export function SpeechLines({
  lines,
  selected,
}: {
  readonly lines: readonly number[];
  readonly selected: boolean;
}): React.JSX.Element {
  return (
    <View style={styles.lines}>
      {lines.map((width, index) => (
        <View
          key={index}
          style={[
            styles.line,
            { width: `${width}%` },
            selected ? styles.lineOn : styles.lineOff,
          ]}
        />
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  lineOn: { opacity: 1, backgroundColor: theme.colour.accentDeep },
  lineOff: { opacity: 0.5, backgroundColor: theme.colour.textFaint },
  row: { flexDirection: 'row', gap: theme.space.sm },
  card: {
    flex: 1,
    minHeight: 96,
    alignItems: 'center',
    justifyContent: 'center',
    gap: theme.space.sm,
    borderRadius: theme.radius.md,
    borderWidth: 2,
    borderColor: 'transparent',
    backgroundColor: theme.colour.surface,
    ...theme.shadow.card,
  },
  selected: {
    borderColor: theme.colour.accent,
    backgroundColor: theme.colour.accentWash,
  },
  pressed: { opacity: 0.85 },
  text: { ...theme.type.subtitle, color: theme.colour.textMuted },
  selectedText: {
    fontFamily: theme.font.strong,
    color: theme.colour.accentDeep,
  },
  lines: { width: 44, height: 26, justifyContent: 'center', gap: 4 },
  line: { height: 4, borderRadius: 2 },
});
