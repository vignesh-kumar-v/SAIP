import { useCallback, useEffect, useRef, useState } from "react";
import { get } from "./api";

/** GET a JSON route; `reload()` fetches again. Optional polling while the component is shown. */
export function useJson<T>(path: string | null, pollMs?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const alive = useRef(true);
  const reload = useCallback(async () => {
    if (!path) return null;
    setLoading(true);
    try {
      const d = await get<T>(path);
      if (alive.current) {
        setData(d);
        setError(null);
      }
      return d;
    } catch (e: any) {
      if (alive.current) setError(e?.message || String(e));
      return null;
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [path]);
  useEffect(() => {
    alive.current = true;
    reload();
    if (!pollMs) return () => void (alive.current = false);
    const t = setInterval(reload, pollMs);
    return () => {
      alive.current = false;
      clearInterval(t);
    };
  }, [reload, pollMs]);
  return { data, error, loading, reload, setData };
}
