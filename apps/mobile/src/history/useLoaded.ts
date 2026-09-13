/**
 * One thing fetched for a screen: loading, failed with a reason, or there.
 *
 * Every history screen reads one resource and offers another try when it fails, and each
 * writing its own three-state effect is how one of them forgets to ignore a late answer after
 * the screen has gone.
 */
import { useCallback, useEffect, useState } from 'react';

export type Loaded<T> =
  | { readonly state: 'loading' }
  | { readonly state: 'failed'; readonly error: unknown }
  | { readonly state: 'ready'; readonly value: T };

export function useLoaded<T>(load: () => Promise<T>): {
  loaded: Loaded<T>;
  retry: () => void;
  refresh: () => void;
} {
  const [loaded, setLoaded] = useState<Loaded<T>>({ state: 'loading' });
  // A refresh reads again behind what is shown: no spinner, and a failure leaves it in place.
  const [attempt, setAttempt] = useState({ count: 0, quietly: false });

  useEffect(() => {
    let current = true;
    if (!attempt.quietly) {
      setLoaded({ state: 'loading' });
    }
    load()
      .then(value => {
        if (current) {
          setLoaded({ state: 'ready', value });
        }
      })
      .catch((error: unknown) => {
        if (current && !attempt.quietly) {
          setLoaded({ state: 'failed', error });
        }
      });
    return () => {
      current = false;
    };
  }, [load, attempt]);

  const retry = useCallback(() => {
    setAttempt(value => ({ count: value.count + 1, quietly: false }));
  }, []);
  const refresh = useCallback(() => {
    setAttempt(value => ({ count: value.count + 1, quietly: true }));
  }, []);

  return { loaded, retry, refresh };
}
