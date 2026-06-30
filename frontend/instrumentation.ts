// Next.js 16 instrumentation hook (standard server startup entry point).
//
// This `register` function runs once on the server before any route handler
// is invoked. It loads the appropriate Sentry config based on the active
// runtime:
//   - "nodejs" → sentry.server.config.ts
//   - "edge"   → sentry.edge.config.ts
//
// The client runtime is intentionally NOT handled here; the client bundle is
// initialized via sentry.client.config.ts, which @sentry/nextjs picks up
// automatically.
//
// Dynamic imports keep the server/edge config out of the client bundle and
// ensure this hook is a no-op when running under `output: "export"` (static
// export has no server runtime).
export async function register(): Promise<void> {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    await import("./sentry.server.config");
  } else if (process.env.NEXT_RUNTIME === "edge") {
    await import("./sentry.edge.config");
  }
}
