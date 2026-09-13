import React from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { theme } from '../theme';
import { Icon } from './icon/Icon';

export interface VoiceOption {
  readonly id: string;
  readonly name: string;
}

interface Props {
  readonly label: string;
  readonly voices: readonly VoiceOption[];
  readonly selected: string | null;
  readonly onChoose: (id: string) => void;
  readonly disabled?: boolean;
}

/**
 * The voices on offer, as faces to tap.
 *
 * Each is its initial rather than a play button. The shipped provider has no samples to play
 * (D-009), and a play control that cannot play is one the design must not draw.
 */
export function VoicePicker({
  label,
  voices,
  selected,
  onChoose,
  disabled = false,
}: Props): React.JSX.Element {
  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      accessibilityRole="radiogroup"
      accessibilityLabel={label}
      contentContainerStyle={styles.row}
    >
      {voices.map(voice => {
        const on = voice.id === selected;
        return (
          <Pressable
            key={voice.id}
            accessibilityRole="radio"
            accessibilityLabel={voice.name}
            accessibilityState={{ selected: on, checked: on, disabled }}
            disabled={disabled}
            onPress={() => {
              onChoose(voice.id);
            }}
            style={styles.voice}
            testID={`voice-option-${voice.id}`}
          >
            <View style={[styles.face, on && styles.faceOn]}>
              <Text style={[styles.initial, on && styles.initialOn]}>
                {voice.name.slice(0, 1).toUpperCase()}
              </Text>
              {on && (
                <View style={styles.badge}>
                  <Icon name="check" colour={theme.colour.onAccent} size={12} />
                </View>
              )}
            </View>
            <Text style={[styles.name, on && styles.nameOn]} numberOfLines={1}>
              {voice.name}
            </Text>
          </Pressable>
        );
      })}
    </ScrollView>
  );
}

const FACE = 68;

const styles = StyleSheet.create({
  row: {
    gap: theme.space.md,
    paddingVertical: theme.space.sm,
    paddingHorizontal: 2,
  },
  voice: { alignItems: 'center', gap: theme.space.sm, width: FACE + 8 },
  face: {
    width: FACE,
    height: FACE,
    borderRadius: FACE / 2,
    backgroundColor: theme.colour.surface,
    alignItems: 'center',
    justifyContent: 'center',
    ...theme.shadow.card,
  },
  faceOn: { backgroundColor: theme.colour.accent, ...theme.shadow.accent },
  initial: {
    ...theme.type.heading,
    fontFamily: theme.font.regular,
    color: theme.colour.textFaint,
  },
  initialOn: { color: theme.colour.onAccent, fontFamily: theme.font.strong },
  badge: {
    position: 'absolute',
    right: 0,
    bottom: 0,
    width: 22,
    height: 22,
    borderRadius: 11,
    backgroundColor: theme.colour.accentDeep,
    borderWidth: 2,
    borderColor: theme.colour.surface,
    alignItems: 'center',
    justifyContent: 'center',
  },
  name: { ...theme.type.caption, color: theme.colour.textMuted },
  nameOn: { fontFamily: theme.font.strong, color: theme.colour.accentDeep },
});
