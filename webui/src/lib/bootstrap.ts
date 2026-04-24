import type { BootstrapResponse } from "./types";

const STORAGE_KEY_SERVER = "nanobot.server";

export interface ConnectionSettings {
  server: string;
  secret: string;
}

const STORAGE_KEY_SECRET = "nanobot.secret";

/** Read cached connection settings from localStorage. */
export function loadConnectionSettings(): ConnectionSettings | null {
  try {
    const server = window.localStorage.getItem(STORAGE_KEY_SERVER);
    const secret = window.sessionStorage.getItem(STORAGE_KEY_SECRET) ?? "";
    if (server) return { server, secret };
  } catch {
    // ignore storage errors
  }
  return null;
}

/** Persist connection settings to localStorage. */
export function saveConnectionSettings(settings: ConnectionSettings): void {
  try {
    window.localStorage.setItem(STORAGE_KEY_SERVER, settings.server);
    // Secret goes to sessionStorage so it doesn't persist across browser sessions.
    if (settings.secret) {
      window.sessionStorage.setItem(STORAGE_KEY_SECRET, settings.secret);
    } else {
      window.sessionStorage.removeItem(STORAGE_KEY_SECRET);
    }
  } catch {
    // ignore storage errors
  }
}

/** Clear cached connection settings. */
export function clearConnectionSettings(): void {
  try {
    window.localStorage.removeItem(STORAGE_KEY_SERVER);
    window.sessionStorage.removeItem(STORAGE_KEY_SECRET);
  } catch {
    // ignore storage errors
  }
}

/** Detect if the page is likely served by the nanobot gateway (same-origin). */
export function isSameOrigin(): boolean {
  try {
    return (
      window.location.hostname === "localhost" ||
      window.location.hostname === "127.0.0.1" ||
      window.location.hostname === "[::1]"
    );
  } catch {
    return false;
  }
}

/**
 * Fetch a short-lived token + the WebSocket path from the gateway's
 * ``/webui/bootstrap`` endpoint.
 *
 * CORS and TLS are handled by an nginx reverse proxy in front of the gateway.
 * When no baseUrl is given, the request goes to the current origin.
 */
export async function fetchBootstrap(
  baseUrl: string = "",
  secret: string = "",
): Promise<BootstrapResponse> {
  const headers: Record<string, string> = {};
  if (secret) {
    headers["Authorization"] = `Bearer ${secret}`;
  }
  const res = await fetch(`${baseUrl}/webui/bootstrap`, {
    method: "GET",
    credentials: "same-origin",
    headers,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `bootstrap failed: HTTP ${res.status}`);
  }
  const body = (await res.json()) as BootstrapResponse;
  if (!body.token || !body.ws_path) {
    throw new Error("bootstrap response missing token or ws_path");
  }
  return body;
}

/**
 * Build a WebSocket URL.
 *
 * If baseUrl is provided (remote mode), derive scheme and host from it.
 * Otherwise fall back to window.location (same-origin / gateway-served mode).
 */
export function deriveWsUrl(
  wsPath: string,
  token: string,
  baseUrl: string = "",
): string {
  const path = wsPath && wsPath.startsWith("/") ? wsPath : `/${wsPath || ""}`;
  const query = `?token=${encodeURIComponent(token)}`;

  if (baseUrl) {
    const url = new URL(baseUrl);
    const scheme = url.protocol === "https:" ? "wss" : "ws";
    return `${scheme}://${url.host}${path}${query}`;
  }

  if (typeof window === "undefined") {
    return `ws://127.0.0.1:8765${path}${query}`;
  }
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  const host = window.location.host;
  return `${scheme}://${host}${path}${query}`;
}
