/** Saves a change as it is made and says why when it is refused. */
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
