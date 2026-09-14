/** Call history for one filter, a page at a time, ignoring answers meant for an earlier list. */
import { useCallback, useEffect, useRef, useState } from 'react';

import type { CallSummary } from '@letmehandle/api-client';

import type { ApiClient } from '../api/client';
import { queryFor, type Filter } from './presentation';
import type { Loaded } from '../api/useLoaded';

export interface CallList {
  readonly calls: readonly CallSummary[];
  /** Where the next page starts, or null when nothing is older. */
  readonly cursor: string | null;
}

export interface CallPages {
  readonly list: Loaded<CallList>;
  readonly loadingMore: boolean;
  retry(): void;
  loadMore(): void;
}

export function useCallPages(
  api: ApiClient,
  filter: Filter,
  refreshKey: number,
): CallPages {
  const [list, setList] = useState<Loaded<CallList>>({ state: 'loading' });
  const [loadingMore, setLoadingMore] = useState(false);
  const [attempt, setAttempt] = useState(0);
  // Bumped whenever the list starts again, so a page for the list before it is dropped.
  const generation = useRef(0);

  useEffect(() => {
    generation.current += 1;
    const mine = generation.current;
    setList({ state: 'loading' });
    setLoadingMore(false);
    api.calls(queryFor(filter)).then(
      page => {
        if (generation.current === mine) {
          setList({
            state: 'ready',
            value: { calls: page.calls, cursor: page.next_cursor },
          });
        }
      },
      (error: unknown) => {
        if (generation.current === mine) {
          setList({ state: 'failed', error });
        }
      },
    );
    return () => {
      generation.current += 1;
    };
  }, [api, filter, attempt, refreshKey]);

  const retry = useCallback(() => {
    setAttempt(value => value + 1);
  }, []);

  const loadMore = useCallback(() => {
    if (list.state !== 'ready' || list.value.cursor === null || loadingMore) {
      return;
    }
    const mine = generation.current;
    const shown = list.value;
    setLoadingMore(true);
    api
      .calls({ ...queryFor(filter), cursor: shown.cursor })
      .then(page => {
        if (generation.current === mine) {
          setList({
            state: 'ready',
            value: {
              calls: [...shown.calls, ...page.calls],
              cursor: page.next_cursor,
            },
          });
        }
      })
      .catch(() => undefined)
      .finally(() => {
        if (generation.current === mine) {
          setLoadingMore(false);
        }
      });
  }, [api, filter, list, loadingMore]);

  return { list, loadingMore, retry, loadMore };
}
