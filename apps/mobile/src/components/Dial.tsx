import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import Svg, { Circle, G } from 'react-native-svg';

import { theme } from '../theme';
import { arcsFor, type Segment } from './dialGeometry';

interface Props {
  readonly size?: number;
  readonly segments?: readonly Segment[];
  /** Where the first segment begins, as a part of the way round from the top. */
  readonly start?: number;
  /** The colour of the part no segment covers. */
  readonly rest?: string;
  /** Draw only a hairline ring: nothing to show yet. */
  readonly blank?: boolean;
  /** What sits in the middle. */
  readonly children?: React.ReactNode;
  readonly testID?: string;
}

/** The ring: segments clockwise from the top or `start`, and a hairline when there is nothing yet. */
export function Dial({
  size = 220,
  segments = [],
  start = 0,
  rest = theme.colour.dialRest,
  blank = false,
  children,
  testID,
}: Props): React.JSX.Element {
  const stroke = Math.round(size * 0.085);
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const centre = size / 2;
  const arcs = arcsFor(segments, circumference);

  return (
    <View style={{ width: size, height: size }} testID={testID}>
      <Svg
        width={size}
        height={size}
        accessibilityElementsHidden
        importantForAccessibility="no-hide-descendants"
      >
        {blank ? (
          <Circle
            cx={centre}
            cy={centre}
            r={radius}
            stroke={theme.colour.border}
            strokeWidth={1.5}
            fill="none"
          />
        ) : (
          // Rotated so zero is at the top, then on to `start`.
          <G rotation={-90 + start * 360} origin={`${centre}, ${centre}`}>
            <Circle
              cx={centre}
              cy={centre}
              r={radius}
              stroke={rest}
              strokeWidth={stroke}
              fill="none"
            />
            {arcs.map((arc, index) => (
              <Circle
                key={index}
                cx={centre}
                cy={centre}
                r={radius}
                stroke={arc.colour}
                strokeWidth={stroke}
                fill="none"
                strokeDasharray={`${arc.length} ${circumference}`}
                strokeDashoffset={-arc.offset}
              />
            ))}
          </G>
        )}
      </Svg>
      <View style={styles.centre}>{children}</View>
    </View>
  );
}

/** The large thin number in the middle of the ring. */
export function DialFigure({
  text,
  testID,
}: {
  readonly text: string;
  readonly testID?: string;
}): React.JSX.Element {
  return (
    <Text style={styles.figure} testID={testID}>
      {text}
    </Text>
  );
}

/** The small capitals under the number. */
export function DialCaption({
  text,
}: {
  readonly text: string;
}): React.JSX.Element {
  return <Text style={styles.caption}>{text}</Text>;
}

const styles = StyleSheet.create({
  figure: { ...theme.type.figure, color: theme.colour.heroText },
  caption: { ...theme.type.label, color: theme.colour.heroMuted, marginTop: 2 },
  centre: {
    ...StyleSheet.absoluteFill,
    alignItems: 'center',
    justifyContent: 'center',
  },
});
