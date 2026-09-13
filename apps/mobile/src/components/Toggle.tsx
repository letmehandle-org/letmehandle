import React from 'react';
import { Switch } from 'react-native';

import { theme } from '../theme';
import type { IconName } from './icon/Icon';
import { Row } from './Row';

interface Props {
  readonly label: string;
  readonly value: boolean;
  readonly onChange: (value: boolean) => void;
  readonly icon?: IconName;
  readonly disabled?: boolean;
  readonly last?: boolean;
  readonly testID?: string;
}

/**
 * A single yes or no, as a row.
 *
 * The label is given to the switch itself rather than sitting beside it as decoration, because
 * a switch announced as "off" with no idea what is off is a control nobody can use without
 * sight.
 */
export function Toggle({
  label,
  value,
  onChange,
  icon,
  disabled = false,
  last,
  testID,
}: Props): React.JSX.Element {
  return (
    <Row
      title={label}
      icon={icon}
      tone={value ? 'assistant' : 'quiet'}
      last={last}
      trailing={
        <Switch
          accessibilityRole="switch"
          accessibilityLabel={label}
          accessibilityState={{ checked: value, disabled }}
          testID={testID}
          value={value}
          disabled={disabled}
          onValueChange={onChange}
          trackColor={{
            true: theme.colour.accent,
            false: theme.colour.textGhost,
          }}
          thumbColor={theme.colour.surface}
          ios_backgroundColor={theme.colour.textGhost}
        />
      }
    />
  );
}
