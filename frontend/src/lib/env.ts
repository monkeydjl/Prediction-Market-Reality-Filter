export function getApiBase(): string {
  const base = process.env.NEXT_PUBLIC_API_BASE ?? "/api";
  if (base === "/api" && isLocalStaticFrontend()) return "http://localhost:8000/api";
  if (base.startsWith("/")) return base;
  try {
    const url = new URL(base);
    if (url.protocol === "http:" || url.protocol === "https:") return base.replace(/\/$/, "");
  } catch {
    // Fall through to the explicit error below.
  }
  throw new Error("NEXT_PUBLIC_API_BASE must be a relative path or an http(s) URL");
}

function isLocalStaticFrontend(): boolean {
  if (typeof window === "undefined") return false;
  if (process.env.NODE_ENV !== "production") return false;
  return (
    window.location.port === "3000" &&
    ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname)
  );
}
