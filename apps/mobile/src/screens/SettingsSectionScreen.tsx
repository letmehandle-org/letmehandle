import React, { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { PreferencesUpdate } from '@letmehandle/api-client';

import { describeFailure } from '../api/messages';
import { Button } from '../components/Button';
import { Notice } from '../components/Notice';
import { Screen } from '../components/Screen';
import { usePreferences } from '../preferences/PreferencesProvider';
import type { PreferenceSection } from '../preferences/options';
import { SectionEditor } from '../preferences/sections';

interface Props {
  readonly section: PreferenceSection;
}

/**
 * One section of the preferences, edited after setup.
 *
 * The same editor onboarding uses. A second copy tuned for settings is how one of the two ends
 * up offering a choice the other does not.
 *
 * A refused save is reported rather than left to the reverted control to imply. The provider
 * has already put the previous value back by the time this runs, and a change that vanishes
 * with no explanation reads as the application losing work.
 */
export function SettingsSectionScreen({ section }: Props): React.JSX.Element {
  const { t } = useTranslation();
  const { preferences, save } = usePreferences();

  const [pending, setPending] = useState<PreferencesUpdate | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  // Stable, because the editor emits its draft from an effect: a new function every render
  // would make that effect run every render.
  const onChange = useCallback((changes: PreferencesUpdate | null): void => {
    setPending(changes);
    setSaved(false);
  }, []);

  const submit = (): void => {
    if (pending === null) {
      return;
    }

    setBusy(true);
    setProblem(null);

    save(pending)
      .then(() => {
        setSaved(true);
      })
      .catch((error: unknown) => {
        setProblem(
          describeFailure(error, t, { refused: t('settings.saveFailed') }),
        );
      })
      .finally(() => {
        setBusy(false);
      });
  };

  return (
    <Screen
      title={t(`preferences.${section}.title`)}
      subtitle={t(`preferences.${section}.subtitle`)}
      scrollable
      testID={`settings-${section}`}
    >
      <SectionEditor
        section={section}
        preferences={preferences}
        onChange={onChange}
      />

      {saved && (
        <Notice message={t('settings.saved')} testID="settings-saved" />
      )}
      {problem !== null && (
        <Notice tone="problem" message={problem} testID="settings-problem" />
      )}

      <Button
        label={t('common.save')}
        onPress={submit}
        busy={busy}
        disabled={pending === null}
        testID="settings-save"
      />
    </Screen>
  );
}
