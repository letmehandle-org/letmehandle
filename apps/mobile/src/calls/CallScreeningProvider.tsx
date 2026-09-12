/**
 * Keeping the handset's screening in step with the signed-in user.
 *
 * Two jobs, both running for as long as somebody is signed in:
 *
 *   The rules snapshot is rewritten whenever the preferences change, and when the app starts.
 *   The screening service reads only that copy, so a rule changed on this screen reaches the
 *   next call without anything else happening.
 *
 *   What the handset observed about its calls is reported: at start, whenever the app returns to
 *   the front, and whenever the native side says something is waiting.
 *
 * Renders its children whether or not the handset can screen. Where it cannot, there is nothing
 * to keep in step and the context says so.
 */
import React, {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';
import { AppState } from 'react-native';

import { useSession } from '../auth/SessionProvider';
import { usePreferences } from '../preferences/PreferencesProvider';
import { CallReporter } from './CallReporter';
import type { CallScreening } from './callScreening';
import { buildRulesSnapshot } from './wire';

export interface CallScreeningValue {
  /** This handset's call screening, or null where it has none. */
  readonly screening: CallScreening | null;
  /** Whether the latest rules failed to reach the handset, which is then applying older ones. */
  readonly rulesNotSaved: boolean;
}

const CallScreeningContext = createContext<CallScreeningValue>({
  screening: null,
  rulesNotSaved: false,
});

export function useCallScreening(): CallScreeningValue {
  return useContext(CallScreeningContext);
}

export function CallScreeningProvider({
  screening,
  children,
}: {
  readonly screening: CallScreening | null;
  readonly children: React.ReactNode;
}): React.JSX.Element {
  const { api } = useSession();
  const { preferences } = usePreferences();
  const [rulesNotSaved, setRulesNotSaved] = useState(false);

  useEffect(() => {
    if (screening === null) {
      return;
    }
    let cancelled = false;
    screening
      .writeRulesSnapshot(buildRulesSnapshot(preferences, new Date()))
      .then(
        () => {
          if (!cancelled) {
            setRulesNotSaved(false);
          }
        },
        () => {
          // Said on the screening screen rather than thrown: the handset keeps applying the rules
          // it had, which is a degraded state the user should know about, not a crash.
          if (!cancelled) {
            setRulesNotSaved(true);
          }
        },
      );
    return () => {
      cancelled = true;
    };
  }, [screening, preferences]);

  useEffect(() => {
    if (screening === null) {
      return;
    }
    const reporter = new CallReporter(screening, api);
    const drain = (): void => {
      // A failed drain leaves every event on the handset for the next one; there is nothing
      // more useful to do with the failure than to try again at the next trigger.
      reporter.drain().catch(() => undefined);
    };
    drain();
    const pending = screening.onCallEventsPending(drain);
    const foreground = AppState.addEventListener('change', state => {
      if (state === 'active') {
        drain();
      }
    });
    return () => {
      pending.remove();
      foreground.remove();
    };
  }, [screening, api]);

  const value = useMemo(
    () => ({ screening, rulesNotSaved }),
    [screening, rulesNotSaved],
  );

  return (
    <CallScreeningContext.Provider value={value}>
      {children}
    </CallScreeningContext.Provider>
  );
}
