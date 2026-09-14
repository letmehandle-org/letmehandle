import React from 'react';
import Svg, { Path } from 'react-native-svg';

import { theme } from '../../theme';

interface Props {
  readonly size?: number;
}

/** The LetMeHandle mark from `brand/source/mark.svg`; decorative, as the name is always written by it. */
export function BrandMark({ size = 44 }: Props): React.JSX.Element {
  return (
    <Svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      accessibilityElementsHidden
      importantForAccessibility="no-hide-descendants"
    >
      <Path
        d="M8 13V39M17 23V39M17 28a5 5 0 0 1 10 0V39M27 28a5 5 0 0 1 10 0V39M46 13V39M46 28a5 5 0 0 1 10 0V39"
        stroke={theme.colour.accent}
        strokeWidth={4.8}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <Path
        d="M20 47.5q12 7 24 0"
        stroke={theme.colour.needsYou}
        strokeWidth={4.4}
        strokeLinecap="round"
      />
    </Svg>
  );
}

/** The smile alone, as an underline: the same curve as the mark's, stretched to a word. */
export function Smile({
  width,
}: {
  readonly width: number;
}): React.JSX.Element {
  return (
    <Svg
      width={width}
      height={22}
      viewBox={`0 0 ${width} 22`}
      fill="none"
      accessibilityElementsHidden
      importantForAccessibility="no-hide-descendants"
    >
      <Path
        d={`M6 6q${(width - 12) / 2} 22 ${width - 12} 0`}
        stroke={theme.colour.needsYou}
        strokeWidth={6}
        strokeLinecap="round"
      />
    </Svg>
  );
}
