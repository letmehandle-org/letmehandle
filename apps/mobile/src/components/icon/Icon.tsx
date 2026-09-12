import React from 'react';
import Svg, { Circle, Path, Rect } from 'react-native-svg';

import { theme } from '../../theme';
import { ICONS, type IconName } from './shapes';

interface Props {
  readonly name: IconName;
  readonly size?: number;
  readonly colour?: string;
  /** Set only when the icon is the whole of what a control says; otherwise it is decoration. */
  readonly label?: string;
  readonly testID?: string;
}

/**
 * One mark from the icon set.
 *
 * Decorative unless given a label. Nearly every icon here sits beside words that already say
 * what it means, and announcing "bot, image" before every row would make the screen reader
 * slower without telling anybody anything.
 */
export function Icon({
  name,
  size = 20,
  colour = theme.colour.textMuted,
  label,
  testID,
}: Props): React.JSX.Element {
  // The stroke is drawn at the design's 1.5 on a 24-point grid, so it scales with the icon.
  return (
    <Svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke={colour}
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      accessible={label !== undefined}
      accessibilityLabel={label}
      accessibilityElementsHidden={label === undefined}
      importantForAccessibility={
        label === undefined ? 'no-hide-descendants' : 'yes'
      }
      testID={testID}
    >
      {ICONS[name].map((shape, index) => {
        switch (shape.kind) {
          case 'path':
            return <Path key={index} d={shape.d} />;
          case 'circle':
            return (
              <Circle key={index} cx={shape.cx} cy={shape.cy} r={shape.r} />
            );
          case 'rect':
            return (
              <Rect
                key={index}
                x={shape.x}
                y={shape.y}
                width={shape.width}
                height={shape.height}
                rx={shape.rx}
              />
            );
        }
      })}
    </Svg>
  );
}

export type { IconName };
