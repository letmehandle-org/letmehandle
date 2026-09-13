/** Keeps the handset's recording, rules snapshot and call reports in step with the signed-in user. */
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
  /** Whether the latest rules were refused by the handset, which then lets every call ring. */
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
    // A failure leaves the handset not recording; the next start asks again.
    screening?.startRecordingCalls().catch(() => undefined);
  }, [screening]);

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
          // The screening screen shows that the handset let every call ring.
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
      // A failed drain leaves every event for the next trigger.
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
