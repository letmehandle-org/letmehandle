/**
 * Saving a change the moment it is made, and saying so when it is refused.
 *
 * The design has no save buttons: a switch is moved and that is the change. The provider already
 * shows the change before the server confirms it and puts it back if refused; what is left for a
 * screen is to tell the user, because a control that springs back with no word reads as the app
 * losing their work.
 */
import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { PreferencesUpdate } from '@letmehandle/api-client';

import { describeFailure } from '../api/messages';
import { usePreferences } from './PreferencesProvider';

export interface ImmediateSave {
  readonly problem: string | null;
  readonly busy: boolean;
  save(changes: PreferencesUpdate): void;
}

export function useImmediateSave(): ImmediateSave {
  const { t } = useTranslation();
  const { save: store } = usePreferences();
  const [problem, setProblem] = useState<string | null>(null);
  const [saving, setSaving] = useState(0);

  const save = useCallback(
    (changes: PreferencesUpdate): void => {
      setProblem(null);
      setSaving(count => count + 1);
      store(changes)
        .catch((error: unknown) => {
          setProblem(
            describeFailure(error, t, { refused: t('common.saveFailed') }),
          );
        })
        .finally(() => {
          setSaving(count => count - 1);
        });
    },
    [store, t],
  );

  return { problem, busy: saving > 0, save };
}
