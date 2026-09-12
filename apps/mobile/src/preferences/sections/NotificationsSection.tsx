import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { StyleSheet, View } from 'react-native';

import type { Notifications } from '@letmehandle/api-client';

import { Toggle } from '../../components/Toggle';
import { theme } from '../../theme';
import type { SectionProps } from './types';

/**
 * The switches, in the order they are asked about.
 *
 * Listed rather than derived from the object's keys: key order is not something to display a
 * form in, and a field added upstream would appear here unlabelled.
 */
const SWITCHES = [
  'on_handled_call',
  'on_blocked_call',
  'on_missed_escalation',
  'daily_summary',
  'respect_quiet_hours',
] as const satisfies readonly (keyof Notifications)[];

/**
 * What is worth interrupting somebody for.
 *
 * Being fetched during a call is not here, because it is not optional: a phone ringing with no
 * way to know why is worse than no assistant at all.
 */
export function NotificationsSection({
  preferences,
  onChange,
}: SectionProps): React.JSX.Element {
  const { t } = useTranslation();
  const [settings, setSettings] = useState<Notifications>(
    preferences.notifications,
  );

  useEffect(() => {
    onChange({ notifications: settings });
  }, [settings, onChange]);

  return (
    <View style={styles.section}>
      {SWITCHES.map(name => (
        <Toggle
          key={name}
          label={t(`preferences.notifications.${name}`)}
          value={settings[name]}
          onChange={on => {
            setSettings(current => ({ ...current, [name]: on }));
          }}
          testID={`notification-${name}`}
        />
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  section: { gap: theme.space.sm },
});
