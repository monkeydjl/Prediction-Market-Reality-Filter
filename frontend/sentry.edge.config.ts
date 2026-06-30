import * as Sentry from "@sentry/nextjs";

// Edge runtime Sentry initialization.
//
// Loaded by instrumentation.ts `register()` when running on the Edge runtime.
// Edge runtime does not support Session Replay, so only core options are set.
// When the DSN is empty, init is skipped so all Sentry calls become no-ops.
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
