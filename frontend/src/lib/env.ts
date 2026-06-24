export function getApiBase(): string {
  const base = process.env.NEXT_PUBLIC_API_BASE ?? "/api";
  if (base.startsWith("/")) return base;
  try {
    const url = new URL(base);
    if (url.protocol === "http:" || url.protocol === "https:") return base.replace(/\/$/, "");
  } catch {
    // Fall through to the explicit error below.
  }
  throw new Error("NEXT_PUBLIC_API_BASE must be a relative path or an http(s) URL");
}

/**
 * Get the API base URL for World Cup components.
 *
 * World Cup fetch calls already include the ``/api`` prefix in their paths
 * (e.g. ``${base}/api/world-cup/predictions/matches``), so this function
 * returns the origin WITHOUT the ``/api`` suffix to avoid double-prefixing.
 *
 * - ``NEXT_PUBLIC_API_BASE`` unset or ``"/api"`` → returns ``""`` (relative)
 * - ``NEXT_PUBLIC_API_BASE`` = ``"https://host/api"`` → returns ``"https://host"``
 * - ``NEXT_PUBLIC_API_BASE`` = ``"https://host"`` → returns ``"https://host"``
 *
 * Previously, World Cup components used NEXT_PUBLIC_API_BASE_URL or
 * NEXT_PUBLIC_API_URL with a hardcoded http://localhost:8000 fallback,
 * which broke in production.
 */
export function getWorldCupApiBase(): string {
  const base = process.env.NEXT_PUBLIC_API_BASE ?? "/api";
  // Relative path "/api" → empty string (fetch paths already include /api/)
  if (base === "/api") return "";
  // Strip trailing /api or /
  return base.replace(/\/api\/?$/, "").replace(/\/$/, "");
}
