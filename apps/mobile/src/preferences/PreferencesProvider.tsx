/**
 * How this user wants calls handled, for the whole signed-in application.
 *
 * Loaded once and held here, because onboarding and settings are the same preferences seen from
 * two angles: a screen that fetched its own copy would show a value another screen had already
 * changed.
 *
 * The provider renders nothing else until the load has settled. That is what lets every screen
 * below it take the preferences as given rather than checking for null on each read — and it
 * means the one place that has to think about "not loaded yet" is this file.
 */
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
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
import { useSession } from '../auth/SessionProvider';
import { Button } from '../components/Button';
import { Notice } from '../components/Notice';
import { Screen } from '../components/Screen';
import { theme } from '../theme';
import { applyChanges, withWholeCallRules } from './changes';

interface Loaded {
  readonly preferences: Preferences;
  readonly onboarding: Onboarding;
}

export interface PreferencesValue extends Loaded {
  /**
   * Save a change, showing it immediately.
   *
   * Rejects when the server refuses, having already put the previous value back. It rejects
   * rather than swallowing because a silent revert is the worst of the three outcomes: the user
   * sees their change disappear and is told nothing about why.
   */
  save(changes: PreferencesUpdate): Promise<void>;
  /** Record an onboarding step as answered, or deliberately passed over. */
  recordStep(step: OnboardingStep, skipped: boolean): Promise<void>;
}

const PreferencesContext = createContext<PreferencesValue | null>(null);

export function usePreferences(): PreferencesValue {
  const value = useContext(PreferencesContext);
  if (value === null) {
    // Reached only by a screen rendered outside the provider, which would otherwise fail later
    // and somewhere unrelated. This says where the mistake is.
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

  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  // Bumped to ask for another go. A boolean would not fire the effect a second time after a
  // failure, which is the only moment anybody presses the button.
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;

    const load = async (): Promise<void> => {
      try {
        const [preferences, onboarding] = await Promise.all([
          api.preferences(),
          api.onboarding(),
        ]);
        if (!cancelled) {
          setLoaded({ preferences, onboarding });
        }
      } catch {
        // Which failure it was does not change what can be offered, and the application below
        // cannot render without these. Retrying is the only useful answer.
        if (!cancelled) {
          setUnavailable(true);
        }
      }
    };

    load().catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [api, attempt]);

  if (unavailable) {
    return (
      <Screen title={t('common.appName')} testID="preferences-unavailable">
        <Notice tone="problem" message={t('onboarding.loadFailed')} />
        <Button
          label={t('common.tryAgain')}
          onPress={() => {
            setUnavailable(false);
            setAttempt(current => current + 1);
          }}
          testID="preferences-retry"
        />
      </Screen>
    );
  }

  if (loaded === null) {
    return (
      <View style={styles.loading} testID="preferences-loading">
        <ActivityIndicator color={theme.colour.accent} />
      </View>
    );
  }

  return (
    <LoadedPreferences api={api} initial={loaded}>
      {children}
    </LoadedPreferences>
  );
}

/**
 * The part that can only exist once there is something to hold.
 *
 * Split out so that `save` has a previous value to roll back to without asking whether there is
 * one. A provider that held `Preferences | null` would need that question answered on every
 * call, and the answer would be "this cannot happen" written six times.
 */
function LoadedPreferences({
  api,
  initial,
  children,
}: {
  readonly api: ApiClient;
  readonly initial: Loaded;
  readonly children: React.ReactNode;
}): React.JSX.Element {
  const [preferences, setPreferencesState] = useState(initial.preferences);
  const [onboarding, setOnboarding] = useState(initial.onboarding);

  // A ref beside the state, for the same reason the session keeps one: a save reads the value
  // that is current now, and a closure over the state would read the one from the render the
  // button was drawn in — which, for two saves in a row, is the value before the first.
  const snapshot = useRef(initial.preferences);

  const setPreferences = useCallback((next: Preferences): void => {
    snapshot.current = next;
    setPreferencesState(next);
  }, []);

  const save = useCallback(
    async (changes: PreferencesUpdate): Promise<void> => {
      const previous = snapshot.current;
      const request = withWholeCallRules(previous, changes);

      // Shown before it is saved, so the control the user just moved stays where they moved it.
      setPreferences(applyChanges(previous, request));

      try {
        setPreferences(await api.updatePreferences(request));
      } catch (failure) {
        setPreferences(previous);
        throw failure;
      }
    },
    [api, setPreferences],
  );

  const recordStep = useCallback(
    async (step: OnboardingStep, skipped: boolean): Promise<void> => {
      // Not optimistic, unlike a preference. Moving to the next question before the server has
      // agreed means showing a question and then taking it back, and the backend refuses to
      // skip a step that has no safe default.
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
