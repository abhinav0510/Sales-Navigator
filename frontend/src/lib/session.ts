/**
 * Session storage for the access token.
 *
 * v1 keeps the short-lived JWT in localStorage for simplicity. The hardening
 * path (LLD 6.15) is a rotating refresh token in an httpOnly cookie; only this
 * file and the login/signup handlers change when that lands.
 */
import { useMemo, useSyncExternalStore } from "react";

export type Session = {
  access_token: string;
  user: { id: string; email: string; full_name: string };
  workspace_id: string;
  workspace_name: string;
  role: string;
};

const KEY = "sn.session";

export function getSession(): Session | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

export function saveSession(s: Session) {
  window.localStorage.setItem(KEY, JSON.stringify(s));
}

export function clearSession() {
  try {
    window.localStorage.removeItem(KEY);
  } catch {
    /* storage unavailable */
  }
}

export function isAdmin(s: Session | null) {
  return !!s && ["admin", "owner", "platform_admin"].includes(s.role);
}

/**
 * React hook: the current session, re-rendering when it changes (login/logout in
 * any tab). Reads the raw string so the snapshot is stable between renders.
 */
function subscribe(onChange: () => void) {
  window.addEventListener("storage", onChange);
  return () => window.removeEventListener("storage", onChange);
}

export function useSession(): Session | null {
  const raw = useSyncExternalStore(
    subscribe,
    () => window.localStorage.getItem(KEY),
    () => null, // server render: no session
  );
  return useMemo(() => {
    try {
      return raw ? (JSON.parse(raw) as Session) : null;
    } catch {
      return null;
    }
  }, [raw]);
}
