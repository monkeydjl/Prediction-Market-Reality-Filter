import * as Sentry from "@sentry/nextjs";

// Server-side (Node.js runtime) Sentry initialization.
//
// Loaded by instrumentation.ts `register()` on server startup. Reads the
// non-public SENTRY_DSN (server-only secret). When the DSN is empty, init is
// skipped so all Sentry calls become no-ops.
const SENTRY_DSN = process.env.SENTRY_DSN || "";

if (SENTRY_DSN) {
  Sentry.init({
    dsn: SENTRY_DSN,
    environment: process.env.SENTRY_ENVIRONMENT || "production",
    release: process.env.SENTRY_RELEASE || "pmrf-frontend@0.3.0",
    tracesSampleRate: Number(process.env.SENTRY_TRACES_SAMPLE_RATE || 0.0),
    // No PII by default.
    sendDefaultPii: false,
  });
}
