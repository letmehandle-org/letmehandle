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

/** A yes-or-no row whose label belongs to the switch itself. */
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
