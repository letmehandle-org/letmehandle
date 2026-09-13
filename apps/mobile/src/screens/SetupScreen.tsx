import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { StyleSheet, Text, View } from 'react-native';

import type {
  Capability,
  OnboardingStep,
  PreferencesUpdate,
  TimeWindow,
} from '@letmehandle/api-client';

import { useSession } from '../auth/SessionProvider';
import { describeFailure } from '../api/messages';
import { Button } from '../components/Button';
import { CallGraph } from '../components/CallGraph';
import { Card } from '../components/Card';
import { HoursEditor } from '../components/HoursEditor';
import { Icon } from '../components/icon/Icon';
import { Lane } from '../components/Lanes';
import { Notice } from '../components/Notice';
import { Screen } from '../components/Screen';
import { Steps } from '../components/Steps';
import { Toggle } from '../components/Toggle';
import { usePreferences } from '../preferences/PreferencesProvider';
import { CAPABILITIES, CAPABILITY_ICONS } from '../preferences/options';
import { twoLanes } from '../preferences/rules';
import { theme } from '../theme';

interface Props {
  readonly step: OnboardingStep;
}

/** The steps setup asks, in the order the server asks them (D-032). */
export const SETUP_STEPS = [
  'call_handling',
  'call_forwarding',
  'hours',
  'authority',
  'notifications',
] as const satisfies readonly OnboardingStep[];

export function SetupScreen({ step }: Props): React.JSX.Element {
  const { t } = useTranslation();
  const { preferences, onboarding, save, recordStep } = usePreferences();
  const { profile } = useSession();
  const [granted, setGranted] = useState<readonly Capability[]>(
    preferences.authority.capabilities ?? [],
  );
  const [hours, setHours] = useState<TimeWindow | null>(
    preferences.hours.active ?? null,
  );
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Forwarding is set on the phone's own carrier settings, so that step saves nothing here.
  const changes: PreferencesUpdate | null = {
    call_handling: { call_handling: twoLanes(preferences.call_handling) },
    call_forwarding: null,
    hours: { hours: { active: hours } },
    authority: { authority: { capabilities: [...granted] } },
    // The graph is the rule itself and asks nothing; the notifications stand as they are.
    notifications: { notifications: preferences.notifications },
  }[step];

  const answer = (): void => {
    setBusy(true);
    setProblem(null);
    (changes === null ? Promise.resolve() : save(changes))
      .then(() => recordStep(step, false))
      .catch((error: unknown) => {
        setProblem(
          describeFailure(error, t, { refused: t('setup.saveFailed') }),
        );
      })
      .finally(() => {
        setBusy(false);
      });
  };

  // Only the steps this deployment asks: forwarding is asked only where calls arrive forwarded.
  const asked = new Set<OnboardingStep>([
    ...onboarding.completed,
    ...onboarding.skipped,
    ...onboarding.remaining,
  ]);
  const steps = SETUP_STEPS.filter(each => asked.has(each));
  const index = steps.indexOf(step) + 1;

  return (
    <Screen
      scrollable
      testID={`onboarding-${step}`}
      header={
        <Steps
          current={index}
          total={steps.length}
          label={t('setup.progress', {
            done: index,
            total: steps.length,
          })}
        />
      }
      title={t(
        {
          call_handling: 'setup.who.title',
          call_forwarding: 'setup.forwarding.title',
          hours: 'setup.hours.title',
          authority: 'setup.authority.title',
          notifications: 'setup.when.title',
        }[step],
      )}
      footer={
        <>
          {problem !== null && (
            <Notice
              tone="problem"
              message={problem}
              testID="onboarding-problem"
            />
          )}
          <Button
            label={t('common.next')}
            onPress={answer}
            busy={busy}
            testID="onboarding-continue"
          />
        </>
      }
    >
      {step === 'call_handling' && <WhoGetsThrough />}
      {step === 'call_forwarding' && (
        <Card>
          <Text style={styles.body}>{t('setup.forwarding.how')}</Text>
          <Text style={styles.number} testID="forwarding-number">
            {profile?.call_forwarding?.number ?? ''}
          </Text>
          <Text style={styles.body}>{t('setup.forwarding.why')}</Text>
        </Card>
      )}
      {step === 'hours' && <HoursEditor value={hours} onChange={setHours} />}
      {step === 'notifications' && <CallGraph />}
      {step === 'authority' && (
        <Card>
          {CAPABILITIES.map((capability, position) => (
            <Toggle
              key={capability}
              icon={CAPABILITY_ICONS[capability]}
              label={t(`preferences.capability.${capability}`)}
              value={granted.includes(capability)}
              onChange={on => {
                setGranted(current =>
                  on
                    ? [...current, capability]
                    : current.filter(entry => entry !== capability),
                );
              }}
              last={position === CAPABILITIES.length - 1}
              testID={`capability-${capability}`}
            />
          ))}
        </Card>
      )}
    </Screen>
  );
}

/** The two lanes and the spam line, shared by setup and settings. */
export function WhoGetsThrough(): React.JSX.Element {
  const { t } = useTranslation();
  return (
    <View style={styles.lanes}>
      <Lane
        from={{ icon: 'users', label: t('lanes.contacts') }}
        to={{ icon: 'phone', label: t('lanes.ringYou') }}
        testID="lane-contacts"
      />
      <Lane
        assistant
        from={{ icon: 'help', label: t('lanes.unknown') }}
        to={{ icon: 'bot', label: t('lanes.assistant') }}
        testID="lane-unknown"
      />
      <View style={styles.aside}>
        <Icon name="ban" colour={theme.colour.textFaint} size={18} />
        <Text style={styles.asideText}>{t('lanes.spam')}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  lanes: { gap: theme.space.md, paddingTop: theme.space.sm },
  aside: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: theme.space.sm,
  },
  asideText: { ...theme.type.subtitle, color: theme.colour.textFaint },
  body: { ...theme.type.subtitle, color: theme.colour.text },
  number: {
    ...theme.type.label,
    color: theme.colour.text,
    marginVertical: theme.space.sm,
  },
});
