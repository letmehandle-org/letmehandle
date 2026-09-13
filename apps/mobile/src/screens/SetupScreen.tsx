import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';

import type {
  Capability,
  OnboardingStep,
  PreferencesUpdate,
} from '@letmehandle/api-client';

import { describeFailure } from '../api/messages';
import { Button } from '../components/Button';
import { CallGraph } from '../components/CallGraph';
import { Card } from '../components/Card';
import { Dial, DialCaption, DialFigure } from '../components/Dial';
import { Hero } from '../components/Hero';
import { Icon } from '../components/icon/Icon';
import { Lane } from '../components/Lanes';
import { Notice } from '../components/Notice';
import { Screen } from '../components/Screen';
import { Steps } from '../components/Steps';
import { Toggle } from '../components/Toggle';
import { usePreferences } from '../preferences/PreferencesProvider';
import { CAPABILITIES, CAPABILITY_ICONS } from '../preferences/options';
import { AROUND_THE_CLOCK, twoLanes } from '../preferences/rules';
import { theme } from '../theme';

interface Props {
  readonly step: OnboardingStep;
}

/**
 * The steps somebody actually sees, in the order the server asks them.
 *
 * The server still has seven steps. The design has four: the introduction is the welcome page
 * before sign-in, and important contacts and personality are optional settings with safe
 * defaults. Those three are recorded without asking, so setup is as short as the design says.
 */
export const VISIBLE_STEPS = [
  'call_handling',
  'hours',
  'authority',
  'notifications',
] as const;

type VisibleStep = (typeof VISIBLE_STEPS)[number];

function isVisible(step: OnboardingStep): step is VisibleStep {
  return (VISIBLE_STEPS as readonly string[]).includes(step);
}

export function SetupScreen({ step }: Props): React.JSX.Element {
  if (!isVisible(step)) {
    return <PassOver step={step} />;
  }
  return <VisibleSetupStep step={step} />;
}

/**
 * A step the design does not ask: recorded and moved past.
 *
 * The introduction is answered, since the welcome page already said it; the other two are
 * skipped, which the backend allows because their defaults are safe.
 */
function PassOver({
  step,
}: {
  readonly step: OnboardingStep;
}): React.JSX.Element {
  const { t } = useTranslation();
  const { recordStep } = usePreferences();
  const [problem, setProblem] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    setProblem(null);
    recordStep(step, step !== 'introduction').catch((error: unknown) => {
      setProblem(describeFailure(error, t, { refused: t('setup.saveFailed') }));
    });
  }, [step, recordStep, t, attempt]);

  return (
    <Screen testID={`onboarding-${step}`}>
      <View style={styles.centre}>
        {problem === null ? (
          <ActivityIndicator color={theme.colour.accent} />
        ) : (
          <>
            <Notice
              tone="problem"
              message={problem}
              testID="onboarding-problem"
            />
            <Button
              label={t('common.tryAgain')}
              onPress={() => {
                setAttempt(current => current + 1);
              }}
              testID="onboarding-retry"
            />
          </>
        )}
      </View>
    </Screen>
  );
}

function VisibleSetupStep({
  step,
}: {
  readonly step: VisibleStep;
}): React.JSX.Element {
  const { t } = useTranslation();
  const { preferences, save, recordStep } = usePreferences();
  const [granted, setGranted] = useState<readonly Capability[]>(
    preferences.authority.capabilities ?? [],
  );
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const changes: PreferencesUpdate = {
    call_handling: { call_handling: twoLanes(preferences.call_handling) },
    hours: { hours: AROUND_THE_CLOCK },
    authority: { authority: { capabilities: [...granted] } },
    // The graph is the rule itself and asks nothing; the notifications stand as they are.
    notifications: { notifications: preferences.notifications },
  }[step];

  const answer = (): void => {
    setBusy(true);
    setProblem(null);
    save(changes)
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

  const index = VISIBLE_STEPS.indexOf(step) + 1;

  return (
    <Screen
      scrollable
      testID={`onboarding-${step}`}
      header={
        <Steps
          current={index}
          total={VISIBLE_STEPS.length}
          label={t('setup.progress', {
            done: index,
            total: VISIBLE_STEPS.length,
          })}
        />
      }
      title={t(
        {
          call_handling: 'setup.who.title',
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
      {step === 'hours' && <AroundTheClock />}
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

/** The full ring that means the assistant works at any hour. */
export function AroundTheClock(): React.JSX.Element {
  const { t } = useTranslation();
  return (
    <Hero testID="hours-always">
      <Dial
        size={210}
        rest={theme.colour.dialRestOnHero}
        segments={[{ fraction: 1, colour: theme.colour.accent }]}
      >
        <Icon name="infinity" colour={theme.colour.accentDeep} size={26} />
        <DialFigure text={t('hours.always')} />
        <DialCaption text={t('hours.alwaysOn')} />
      </Dial>
    </Hero>
  );
}

const styles = StyleSheet.create({
  centre: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: theme.space.md,
  },
  lanes: { gap: theme.space.md, paddingTop: theme.space.sm },
  aside: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: theme.space.sm,
  },
  asideText: { ...theme.type.subtitle, color: theme.colour.textFaint },
});
