// A five-route app does not need a router library: the path, a hook and a link.
import { useEffect, useState, type AnchorHTMLAttributes } from "react";

const listeners = new Set<() => void>();

export function navigate(to: string, replace = false) {
  if (to === location.pathname + location.hash) return;
  if (replace) history.replaceState(null, "", to);
  else history.pushState(null, "", to);
  listeners.forEach((l) => l());
  window.scrollTo({ top: 0 });
}

export function usePath(): string {
  const [path, setPath] = useState(location.pathname);
  useEffect(() => {
    const on = () => setPath(location.pathname);
    listeners.add(on);
    addEventListener("popstate", on);
    return () => {
      listeners.delete(on);
      removeEventListener("popstate", on);
    };
  }, []);
  return path;
}

export function Link({ to, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement> & { to: string }) {
  return (
    <a
      href={to}
      onClick={(e) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
        e.preventDefault();
        navigate(to);
      }}
      {...rest}
    />
  );
}
