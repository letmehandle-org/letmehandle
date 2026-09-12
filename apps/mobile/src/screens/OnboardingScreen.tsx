import React, { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type {
  OnboardingStep,
  PreferencesUpdate,
} from '@letmehandle/api-client';

import { describeFailure } from '../api/messages';
import { Button } from '../components/Button';
import { Notice } from '../components/Notice';
import { Screen } from '../components/Screen';
import { usePreferences } from '../preferences/PreferencesProvider';
import { canSkip } from '../preferences/options';
import { SectionEditor } from '../preferences/sections';

interface Props {
  readonly step: OnboardingStep;
}

/**
 * One question at a time, in the order the server asks them.
 *
 * The step comes from the server's `next_step` rather than from a counter held here, which is
 * what makes the flow resumable: somebody who reinstalls, or signs in on a second phone, is
 * asked what is left rather than everything again.
 *
 * Skipping is offered only where the server allows it. Showing the button on `call_handling`
 * and letting the 422 explain would be showing somebody a failure they could not have avoided.
 */
export function OnboardingScreen({ step }: Props): React.JSX.Element {
  const { t } = useTranslation();
  const { preferences, onboarding, save, recordStep } = usePreferences();

  const [pending, setPending] = useState<PreferencesUpdate | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Stable, because the editor emits its draft from an effect: a new function every render
  // would make that effect run every render.
  const onChange = useCallback((changes: PreferencesUpdate | null): void => {
    setPending(changes);
  }, []);

  const settled = onboarding.completed.length + onboarding.skipped.length;
  const total = settled + onboarding.remaining.length;

  const finish = (promise: Promise<void>): void => {
    setBusy(true);
    setProblem(null);
    promise
      .catch((error: unknown) => {
        setProblem(
          describeFailure(error, t, { refused: t('onboarding.saveFailed') }),
        );
      })
      .finally(() => {
        setBusy(false);
      });
  };

  const answer = (): void => {
    // The introduction asks for nothing, so there is nothing to save before recording it.
    const saved = pending === null ? Promise.resolve() : save(pending);
    finish(saved.then(() => recordStep(step, false)));
  };

  return (
    <Screen
      title={t(`preferences.${step}.title`)}
      subtitle={t(`preferences.${step}.subtitle`)}
      scrollable
      testID={`onboarding-${step}`}
    >
      <Notice
        message={t('onboarding.progress', { done: settled + 1, total })}
        testID="onboarding-progress"
      />

      {step === 'introduction' ? (
        <Notice message={t('preferences.introduction.body')} />
      ) : (
        // Narrowed by the branch above: every step but the introduction is a section.
        <SectionEditor
          section={step}
          preferences={preferences}
          onChange={onChange}
        />
      )}

      {problem !== null && (
        <Notice tone="problem" message={problem} testID="onboarding-problem" />
      )}

      <Button
        label={t('common.continue')}
        onPress={answer}
        busy={busy}
        disabled={step !== 'introduction' && pending === null}
        testID="onboarding-continue"
      />

      {canSkip(step) && (
        <Button
          label={t('onboarding.skip')}
          variant="quiet"
          onPress={() => {
            finish(recordStep(step, true));
          }}
          disabled={busy}
          testID="onboarding-skip"
        />
      )}
    </Screen>
  );
}
