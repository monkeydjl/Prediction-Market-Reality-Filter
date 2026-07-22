/**
 * Browser-side operator write credentials (P2-FE9).
 *
 * Model (current):
 * - Store API_WRITE_KEY mirror + operator id in sessionStorage only
 * - Attach as X-API-Key / X-Operator on write requests (see api.ts / sports-api)
 * - Session-scoped: cleared when the tab/window session ends
 * - Never use localStorage (survives browser restart; higher XSS persistence risk)
 * - Never put the key in URLs, logs, or SWR cache keys
 *
 * Future (architecture upgrade, not implemented here):
 * - BFF session cookie + server-held secret (key never reaches JS)
 */

export const OPERATOR_KEY_STORAGE = "pmrf.operatorApiKey";
export const OPERATOR_ID_STORAGE = "pmrf.operatorId";

/** Dispatched on window when credentials change (same-tab UI sync). */
export const OPERATOR_CREDENTIALS_EVENT = "pmrf:operator-credentials";

/**
 * Dispatched to open the nav OperatorKeyControl edit form (竞猜落地页 deep link).
 * Prefer this over relying on location.hash alone in tests / SPA navigations.
 */
export const OPERATOR_KEY_OPEN_EVENT = "pmrf:open-operator-key";

/** Request the top-bar operator key form to open (and optionally set #operator-key). */
export function requestOpenOperatorKey(options?: { setHash?: boolean }): void {
  if (typeof window === "undefined") return;
  if (options?.setHash !== false) {
    try {
      if (window.location.hash !== "#operator-key") {
        window.location.hash = "operator-key";
      }
    } catch {
      // ignore
    }
  }
  window.dispatchEvent(new Event(OPERATOR_KEY_OPEN_EVENT));
}

export type OperatorCredentialsSnapshot = {
  hasKey: boolean;
  operatorId: string;
  /** Masked key for UI only — never the raw secret */
  keyHint: string;
};

function canUseSessionStorage(): boolean {
  return typeof window !== "undefined" && typeof window.sessionStorage !== "undefined";
}

function read(key: string): string {
  if (!canUseSessionStorage()) return "";
  try {
    return window.sessionStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

function write(key: string, value: string): void {
  if (!canUseSessionStorage()) return;
  try {
    const trimmed = value.trim();
    if (trimmed) window.sessionStorage.setItem(key, trimmed);
    else window.sessionStorage.removeItem(key);
  } catch {
    // Private mode / blocked storage — fail closed (no key sent).
  }
}

function emitChange(): void {
  if (typeof window === "undefined") return;
  try {
    window.dispatchEvent(new Event(OPERATOR_CREDENTIALS_EVENT));
  } catch {
    // ignore
  }
}

export function getOperatorApiKey(): string {
  return read(OPERATOR_KEY_STORAGE);
}

export function setOperatorApiKey(value: string): void {
  write(OPERATOR_KEY_STORAGE, value);
  emitChange();
}

export function getOperatorId(): string {
  return read(OPERATOR_ID_STORAGE);
}

export function setOperatorId(value: string): void {
  write(OPERATOR_ID_STORAGE, value);
  emitChange();
}

export function clearOperatorCredentials(): void {
  if (!canUseSessionStorage()) return;
  try {
    window.sessionStorage.removeItem(OPERATOR_KEY_STORAGE);
    window.sessionStorage.removeItem(OPERATOR_ID_STORAGE);
  } catch {
    // ignore
  }
  emitChange();
}

export function hasOperatorApiKey(): boolean {
  return getOperatorApiKey().length > 0;
}

/** UI-only hint: first/last chars, never full key. */
export function maskOperatorKey(key: string): string {
  const k = key.trim();
  if (!k) return "";
  if (k.length <= 4) return "••••";
  if (k.length <= 8) return `${k.slice(0, 1)}•••${k.slice(-1)}`;
  return `${k.slice(0, 2)}…${k.slice(-2)}`;
}

export function getOperatorCredentialsSnapshot(): OperatorCredentialsSnapshot {
  const key = getOperatorApiKey();
  return {
    hasKey: key.length > 0,
    operatorId: getOperatorId(),
    keyHint: maskOperatorKey(key),
  };
}

/** Headers for write requests. Does not include empty values. */
export function buildOperatorAuthHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  const key = getOperatorApiKey();
  if (key) headers["X-API-Key"] = key;
  const operatorId = getOperatorId();
  if (operatorId) headers["X-Operator"] = operatorId;
  return headers;
}

export function applyOperatorAuthHeaders(headers: Headers): void {
  const auth = buildOperatorAuthHeaders();
  if (auth["X-API-Key"] && !headers.has("X-API-Key")) {
    headers.set("X-API-Key", auth["X-API-Key"]);
  }
  if (auth["X-Operator"] && !headers.has("X-Operator")) {
    headers.set("X-Operator", auth["X-Operator"]);
  }
}
