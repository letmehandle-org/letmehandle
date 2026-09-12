import React from 'react';
import { StyleSheet, View } from 'react-native';

import { theme } from '../theme';
import { Icon, type IconName } from './icon/Icon';

export type Tone = 'assistant' | 'needsYou' | 'quiet' | 'warning';

interface Props {
  readonly icon: IconName;
  readonly tone?: Tone;
  readonly size?: number;
}

/** What each tone paints, so a colour means the same thing on every screen. */
export const TONES: Record<
  Tone,
  { readonly fill: string; readonly ink: string }
> = {
  assistant: { fill: theme.colour.accentWash, ink: theme.colour.accentDeep },
  needsYou: { fill: theme.colour.needsYouWash, ink: theme.colour.needsYouDeep },
  quiet: { fill: theme.colour.quietWash, ink: theme.colour.textMuted },
  warning: { fill: theme.colour.warningWash, ink: theme.colour.warning },
};

/**
 * An icon in a soft circle, coloured by what it means.
 *
 * The tone is the meaning and the icon is the subject: a courier who was handled and a courier
 * who needs the user share the truck, and differ only here.
 */
export function Disc({
  icon,
  tone = 'quiet',
  size = 44,
}: Props): React.JSX.Element {
  const { fill, ink } = TONES[tone];
  return (
    <View
      style={[
        styles.disc,
        {
          width: size,
          height: size,
          borderRadius: size / 2,
          backgroundColor: fill,
        },
      ]}
    >
      <Icon name={icon} colour={ink} size={Math.round(size * 0.46)} />
    </View>
  );
}

const styles = StyleSheet.create({
  disc: { alignItems: 'center', justifyContent: 'center' },
});
