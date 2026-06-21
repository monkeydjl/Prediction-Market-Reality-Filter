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
