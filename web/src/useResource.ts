import { useCallback, useEffect, useRef, useState } from "react";
import { request } from "./workspace";

// Every read owns an abort signal. Navigation, refresh and a successful local
// mutation invalidate stale replies; writes are never silently cancelled.
export function useResource<T>(
  path: string | null,
  refresh: unknown,
  timeoutMs = 10000,
) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(!!path);
  const [key, setKey] = useState({ path, refresh });
  if (key.path !== path || key.refresh !== refresh) {
    setKey({ path, refresh });
    setLoading(!!path);
    setError("");
    if (key.path !== path) setData(null);
  }
  const controller = useRef<AbortController | null>(null);
  const invalidate = useCallback(() => {
    controller.current?.abort();
    setLoading(false);
  }, []);
  useEffect(() => {
    const pending = new AbortController();
    controller.current = pending;
    if (!path) return () => pending.abort();
    request<T>(path, "GET", undefined, pending.signal, timeoutMs)
      .then((result) => {
        if (!pending.signal.aborted) {
          setData(result);
          setError("");
        }
      })
      .catch((error: Error) => {
        if (!pending.signal.aborted) setError(error.message);
      })
      .finally(() => {
        if (!pending.signal.aborted) setLoading(false);
      });
    return () => pending.abort();
  }, [path, refresh, timeoutMs]);
  return { data, setData, error, setError, loading, invalidate };
}

export function usePage<
  P extends {
    items: unknown[];
    next_cursor?: number | null;
    next_before?: number | null;
    next_offset?: number | null;
  },
>(path: string, refresh: unknown) {
  const resource = useResource<P>(path, refresh);
  const { data, setData, setError } = resource;
  const [loadingMore, setLoadingMore] = useState(false);
  const [key, setKey] = useState({ path, refresh });
  if (key.path !== path || key.refresh !== refresh) {
    setKey({ path, refresh });
    setLoadingMore(false);
  }
  const more = useRef<AbortController | null>(null);
  useEffect(() => {
    more.current?.abort();
    more.current = null;
    return () => more.current?.abort();
  }, [path, refresh]);
  async function older() {
    const cursor = data?.next_cursor ?? data?.next_before ?? data?.next_offset;
    if (!cursor || more.current || resource.loading) return;
    const pending = new AbortController();
    more.current = pending;
    setLoadingMore(true);
    try {
      const page = await request<P>(
        `${path}${path.includes("?") ? "&" : "?"}${data?.next_offset != null ? "offset" : "before"}=${cursor}`,
        "GET",
        undefined,
        pending.signal,
      );
      if (!pending.signal.aborted) {
        setData((previous) => ({
          ...page,
          items: [...(previous?.items ?? []), ...page.items],
        }));
        setError("");
      }
    } catch (error) {
      if (!pending.signal.aborted) setError((error as Error).message);
    } finally {
      if (!pending.signal.aborted) {
        more.current = null;
        setLoadingMore(false);
      }
    }
  }
  return { ...resource, loadingMore, older };
}
