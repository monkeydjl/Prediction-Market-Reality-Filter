export function getApiBase(): string {
  const base = process.env.NEXT_PUBLIC_API_BASE ?? "/api";
  if (base === "/api" && isLocalStaticFrontend()) return "http://localhost:8000/api";
  const rootRelativeBase = base.replace(/\/+$/, "") || "/";
  if (
    base.startsWith("/") &&
    (!base.startsWith("//") || rootRelativeBase === "/") &&
    !base.includes("?") &&
    !base.includes("#")
  ) {
    return rootRelativeBase;
  }
  try {
    const url = new URL(base);
    if (
      (url.protocol === "http:" || url.protocol === "https:") &&
      !url.search &&
      !url.hash &&
      !url.username &&
      !url.password
    ) {
      return base.replace(/\/+$/, "");
    }
  } catch {
    // Fall through to the explicit error below.
  }
  throw new Error(
    "NEXT_PUBLIC_API_BASE must be a root-relative path or an http(s) URL without credentials, query or fragment",
  );
}

export function joinApiPath(base: string, path: string): string {
  return `${base === "/" ? "" : base}${path}`;
}

function isLocalStaticFrontend(): boolean {
  if (typeof window === "undefined") return false;
  if (process.env.NODE_ENV !== "production") return false;
  return (
    window.location.port === "3000" &&
    ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname)
  );
}

/**
 * Build the realtime price-socket URL for a match.
 *
 * The backend route lives under the same `/api` prefix as every HTTP endpoint
 * (`/api/ws/matches/{id}/prices`), so the origin resolution reuses `getApiBase`
 * rather than duplicating it: one source of truth for "where is the backend",
 * two protocols. The path segment below is the only part specific to WS.
 *
 * Origin precedence, mirroring what the app already documents for WS:
 * 1. `NEXT_PUBLIC_WS_ORIGIN` — explicit WS override (wins over everything).
 * 2. `NEXT_PUBLIC_API_ORIGIN` — legacy alias, kept for existing deployments.
 * 3. `getApiBase()` — the same origin decision HTTP calls make. A relative
 *    base in a dev tab on :3000 means the FastAPI port :8000 (the Next dev
 *    rewrite only covers HTTP, not WS); a relative base in production means
 *    same-origin (FastAPI serves the static export and the API).
 *
 * `getApiBase()` is the complete externally visible HTTP prefix. Relative and
 * absolute custom prefixes therefore carry over unchanged to the WebSocket
 * route. `NEXT_PUBLIC_WS_ORIGIN` and the legacy `NEXT_PUBLIC_API_ORIGIN` are
 * pure-origin overrides instead: after validation, they use the backend's
 * standard `/api/ws/...` route.
 */
export function buildWsUrl(matchId: string): string {
  const path = `/ws/matches/${encodeURIComponent(matchId)}/prices`;
  const explicitName = process.env.NEXT_PUBLIC_WS_ORIGIN
    ? "NEXT_PUBLIC_WS_ORIGIN"
    : process.env.NEXT_PUBLIC_API_ORIGIN
      ? "NEXT_PUBLIC_API_ORIGIN"
      : null;
  if (explicitName) {
    const value = process.env[explicitName] as string;
    const origin = parseOriginOverride(value, explicitName);
    return `${httpToWs(origin)}/api${path}`;
  }

  const apiBase = getApiBase();
  if (/^https?:\/\//.test(apiBase)) {
    return joinApiPath(httpToWs(apiBase), path);
  }

  const origin = resolveRelativeWsOrigin();
  return `${origin}${joinApiPath(apiBase, path)}`;
}

function parseOriginOverride(value: string, name: string): string {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error(`${name} must be an http(s) or ws(s) origin`);
  }
  if (!["http:", "https:", "ws:", "wss:"].includes(url.protocol)) {
    throw new Error(`${name} must use http(s) or ws(s)`);
  }
  if (url.pathname !== "/" || url.search || url.hash || url.username || url.password) {
    throw new Error(`${name} must be a pure origin without path, query or fragment`);
  }
  return url.origin === "null" ? `${url.protocol}//${url.host}` : url.origin;
}

function httpToWs(url: string): string {
  if (url.startsWith("https://")) return `wss://${url.slice("https://".length)}`;
  if (url.startsWith("http://")) return `ws://${url.slice("http://".length)}`;
  return url.replace(/\/+$/, "");
}

function resolveRelativeWsOrigin(): string {
  if (typeof window === "undefined") return "ws://localhost:8000";
  const { hostname, port, protocol } = window.location;
  const isLocalHost = ["localhost", "127.0.0.1", "::1"].includes(hostname);
  if (port === "3000" || (isLocalHost && port !== "8000" && port !== "")) {
    const wsProto = protocol === "https:" ? "wss" : "ws";
    return `${wsProto}://${hostname}:8000`;
  }
  const origin = `${protocol}//${hostname}${port ? `:${port}` : ""}`;
  return httpToWs(origin);
}
