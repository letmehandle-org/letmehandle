import React, { useEffect, useState } from 'react';
import { StatusBar } from 'react-native';
import {
  SafeAreaProvider,
  initialWindowMetrics,
} from 'react-native-safe-area-context';

import { initialiseI18n } from './i18n';
import { RootNavigator } from './navigation/RootNavigator';

/**
 * The root.
 *
 * Nothing renders until translation is ready. Rendering first and swapping the strings in makes
 * every screen briefly show its keys, which is the kind of thing that reaches a release.
 */
export function App(): React.JSX.Element | null {
  const [ready, setReady] = useState(false);
  const [failure, setFailure] = useState<Error | null>(null);

  useEffect(() => {
    // `cancelled` because the promise can settle after the component has gone, and setting
    // state then is a warning in development and a leak in principle.
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
    // Thrown rather than swallowed. An application that cannot load its own strings has no
    // honest screen to show, and a permanently blank one hides the reason. Phase 9 gives this
    // an error boundary and a designed failure state; until then it must be loud.
    throw failure;
  }

  if (!ready) {
    return null;
  }

  return (
    <SafeAreaProvider initialMetrics={initialWindowMetrics}>
      {/* backgroundColor was removed from StatusBar in React Native 0.87: Android is
          edge-to-edge, and the surface behind the bar is the screen's own background. */}
      <StatusBar barStyle="light-content" />
      <RootNavigator />
    </SafeAreaProvider>
  );
}
