import React, { useEffect, useState } from 'react';
import { StatusBar } from 'react-native';
import {
  SafeAreaProvider,
  initialWindowMetrics,
} from 'react-native-safe-area-context';

import { SessionProvider } from './auth/SessionProvider';
import { initialiseI18n } from './i18n';
import { RootNavigator } from './navigation/RootNavigator';

/** The root: renders nothing until translations are ready, so no screen shows a key. */
export function App(): React.JSX.Element | null {
  const [ready, setReady] = useState(false);
  const [failure, setFailure] = useState<Error | null>(null);

  useEffect(() => {
    let cancelled = false;

    initialiseI18n().then(
      () => {
        if (!cancelled) {
          setReady(true);
        }
      },
      (error: unknown) => {
        if (!cancelled) {
          setFailure(error instanceof Error ? error : new Error(String(error)));
        }
      },
    );

    return () => {
      cancelled = true;
    };
  }, []);

  if (failure !== null) {
    // An app that cannot load its strings throws rather than showing a blank screen.
    throw failure;
  }

  if (!ready) {
    return null;
  }

  return (
    <SafeAreaProvider initialMetrics={initialWindowMetrics}>
      {/* The status bar has no background of its own; the screen draws behind it edge to edge. */}
      <StatusBar barStyle="dark-content" />
      <SessionProvider>
        <RootNavigator />
      </SessionProvider>
    </SafeAreaProvider>
  );
}
