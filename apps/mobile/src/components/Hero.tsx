import React from 'react';
import { StyleSheet, View, type StyleProp, type ViewStyle } from 'react-native';
import Svg, { Defs, LinearGradient, Rect, Stop } from 'react-native-svg';

import { theme } from '../theme';

interface Props {
  readonly children: React.ReactNode;
  readonly style?: StyleProp<ViewStyle>;
  readonly testID?: string;
}

/**
 * The pastel card that carries the ring.
 *
 * The one gradient in the app, drawn with SVG because React Native has no gradient of its own,
 * and kept to this card so that it stays the thing the eye lands on first.
 */
export function Hero({ children, style, testID }: Props): React.JSX.Element {
  return (
    <View style={[styles.hero, style]} testID={testID}>
      <Svg style={StyleSheet.absoluteFill} preserveAspectRatio="none">
        <Defs>
          <LinearGradient id="hero" x1="0" y1="0" x2="1" y2="1">
            <Stop offset="0" stopColor={theme.colour.heroTop} />
            <Stop offset="1" stopColor={theme.colour.heroBottom} />
          </LinearGradient>
        </Defs>
        <Rect width="100%" height="100%" fill="url(#hero)" />
      </Svg>
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  hero: {
    borderRadius: theme.radius.lg,
    overflow: 'hidden',
    alignItems: 'center',
    paddingVertical: theme.space.lg,
    paddingHorizontal: theme.space.md,
    gap: theme.space.md,
  },
});
