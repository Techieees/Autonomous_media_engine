"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError } from "./api";

type ResourceState<T> = {
  data: T | null;
  error: string | null;
  loading: boolean;
  fetchedAt: string | null;
  reload: () => void;
};

export function useResource<T>(
  loader: () => Promise<T>,
  deps: unknown[] = [],
  refreshMs?: number,
): ResourceState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchedAt, setFetchedAt] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  const reload = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    loader()
      .then((result) => {
        if (cancelled) return;
        setData(result);
        setError(null);
        setFetchedAt(new Date().toISOString());
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message =
          err instanceof ApiError
            ? err.message
            : err instanceof Error
              ? err.message
              : "Request failed";
        setError(message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // loader identity is owned by the caller via deps
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, ...deps]);

  useEffect(() => {
    if (!refreshMs) return;
    const id = window.setInterval(() => setTick((n) => n + 1), refreshMs);
    return () => window.clearInterval(id);
  }, [refreshMs]);

  return { data, error, loading, fetchedAt, reload };
}
