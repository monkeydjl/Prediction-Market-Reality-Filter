import * as Sentry from "@sentry/nextjs";

// Client-side Sentry initialization.
//
// DSN is read from NEXT_PUBLIC_SENTRY_DSN so it is inlined into the client
// bundle (Next.js only exposes env vars prefixed with NEXT_PUBLIC_ to the
// browser). When the DSN is empty, Sentry.init is skipped entirely so all
// Sentry calls become no-ops and the app starts normally.
const SENTRY_DSN = process.env.NEXT_PUBLIC_SENTRY_DSN || "";

if (SENTRY_DSN) {
  Sentry.init({
    dsn: SENTRY_DSN,
    environment: process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT || "production",
    release: process.env.NEXT_PUBLIC_SENTRY_RELEASE || "pmrf-frontend@0.3.0",
    // Default 0.0 to stay consistent with the backend sampling policy.
    // Override in production via NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE.
    tracesSampleRate: Number(
      process.env.NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE || 0.0,
    ),
    // Session Replay: never record normal sessions, always record sessions
    // where an error occurs.
    replaysSessionSampleRate: 0.0,
    replaysOnErrorSampleRate: 1.0,
    // Do not send PII by default.
    sendDefaultPii: false,
  });
}
