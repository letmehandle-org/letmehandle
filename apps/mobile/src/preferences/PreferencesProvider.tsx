/** The signed-in user's preferences and onboarding, loaded once and held for every screen below. */
import React, {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator, StyleSheet, View } from 'react-native';

import type {
  Onboarding,
  OnboardingStep,
  Preferences,
  PreferencesUpdate,
} from '@letmehandle/api-client';

import type { ApiClient } from '../api/client';
import { useLoaded } from '../api/useLoaded';
import { useSession } from '../auth/SessionProvider';
import { Button } from '../components/Button';
import { Notice } from '../components/Notice';
import { Screen } from '../components/Screen';
import { theme } from '../theme';
import { applyChanges } from './changes';

interface Loaded {
  readonly preferences: Preferences;
  readonly onboarding: Onboarding;
}

export interface PreferencesValue extends Loaded {
  /** Shows the change at once and rejects, with it removed, when the server refuses. */
  save(changes: PreferencesUpdate): Promise<void>;
  /** Record an onboarding step as answered, or deliberately passed over. */
  recordStep(step: OnboardingStep, skipped: boolean): Promise<void>;
}

const PreferencesContext = createContext<PreferencesValue | null>(null);

export function usePreferences(): PreferencesValue {
  const value = useContext(PreferencesContext);
  if (value === null) {
    throw new Error('usePreferences must be used inside a PreferencesProvider');
  }
  return value;
}

export function PreferencesProvider({
  children,
}: {
  children: React.ReactNode;
}): React.JSX.Element {
  const { t } = useTranslation();
  const { api } = useSession();

  const load = useCallback(async (): Promise<Loaded> => {
    const [preferences, onboarding] = await Promise.all([
      api.preferences(),
      api.onboarding(),
    ]);
    return { preferences, onboarding };
  }, [api]);
  const { loaded, retry } = useLoaded(load);

  if (loaded.state === 'failed') {
    return (
      <Screen title={t('common.appName')} testID="preferences-unavailable">
        <Notice tone="problem" message={t('setup.loadFailed')} />
        <Button
          label={t('common.tryAgain')}
          onPress={retry}
          testID="preferences-retry"
        />
      </Screen>
    );
  }

  if (loaded.state === 'loading') {
    return (
      <View style={styles.loading} testID="preferences-loading">
        <ActivityIndicator color={theme.colour.accent} />
      </View>
    );
  }

  return (
    <LoadedPreferences api={api} initial={loaded.value}>
      {children}
    </LoadedPreferences>
  );
}

/** The provider once preferences are loaded, so nothing below checks for null. */
function LoadedPreferences({
  api,
  initial,
  children,
}: {
  readonly api: ApiClient;
  readonly initial: Loaded;
  readonly children: React.ReactNode;
}): React.JSX.Element {
  const [preferences, setPreferences] = useState(initial.preferences);
  const [onboarding, setOnboarding] = useState(initial.onboarding);

  // The server's last answer, and the changes still saving in the order they were made.
  const confirmed = useRef(initial.preferences);
  const inFlight = useRef(new Map<number, PreferencesUpdate>());
  const lastSave = useRef(0);

  const show = useCallback((): void => {
    setPreferences(
      [...inFlight.current.values()].reduce(applyChanges, confirmed.current),
    );
  }, []);

  const save = useCallback(
    async (changes: PreferencesUpdate): Promise<void> => {
      lastSave.current += 1;
      const id = lastSave.current;
      inFlight.current.set(id, changes);
      show();
      try {
        confirmed.current = await api.updatePreferences(changes);
      } finally {
        inFlight.current.delete(id);
        show();
      }
    },
    [api, show],
  );

  const recordStep = useCallback(
    async (step: OnboardingStep, skipped: boolean): Promise<void> => {
      // Not optimistic: the next step shows only once the server has recorded this one.
      setOnboarding(await api.recordOnboardingStep(step, skipped));
    },
    [api],
  );

  const value = useMemo<PreferencesValue>(
    () => ({ preferences, onboarding, save, recordStep }),
    [preferences, onboarding, save, recordStep],
  );

  return (
    <PreferencesContext.Provider value={value}>
      {children}
    </PreferencesContext.Provider>
  );
}

const styles = StyleSheet.create({
  loading: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: theme.colour.background,
  },
});
