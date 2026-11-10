/**
 * API client - the ONLY place the UI talks to the application tier.
 *
 * - Adds the bearer token from the session to every request.
 * - Turns RFC 7807 problem+json errors into `ApiError` (code + message + request id),
 *   so pages can show a precise message ("Only 5 leads left this month...").
 * - On 401 it clears the session and sends the user to /login.
 */
import { clearSession, getSession } from "./session";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public requestId?: string,
    public extra?: Record<string, unknown>,
  ) {
    super(message);
  }
}

type Options = { method?: string; body?: unknown; raw?: boolean };

export async function api<T = unknown>(path: string, opts: Options = {}): Promise<T> {
  const session = getSession();
  const res = await fetch(`${API_URL}${path}`, {
    method: opts.method ?? (opts.body ? "POST" : "GET"),
    headers: {
      "Content-Type": "application/json",
      ...(session ? { Authorization: `Bearer ${session.access_token}` } : {}),
    },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });

  if (res.status === 401 && session) {
    clearSession();
    // A full reload (not router.push) is intended: it drops every in-memory state of the expired session.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    if (typeof window !== "undefined") window.location.href = "/login";
  }
  if (!res.ok) {
    let problem: { code?: string; detail?: string; request_id?: string; extra?: Record<string, unknown> } = {};
    try {
      problem = await res.json();
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, problem.code ?? "error", problem.detail ?? res.statusText,
      problem.request_id, problem.extra);
  }
  if (opts.raw) return (await res.text()) as T;
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** Download a CSV export with the auth header (a plain <a href> cannot send it). */
export async function downloadCsv(path: string, filename: string) {
  const text = await api<string>(path, { raw: true });
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
