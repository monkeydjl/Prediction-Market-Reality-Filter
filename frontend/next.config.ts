import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs";

const isDev = process.env.NODE_ENV === "development";

// Dev: a normal Next server at `/`; API calls (BASE="/api") are proxied to
// FastAPI via the rewrite below.
// Prod (`next build`): static export into out/, served by FastAPI at the site
// root. `output: export` disables rewrites, so it must NOT be set in dev.
const nextConfig: NextConfig = isDev
  ? {
      images: { unoptimized: true },
      async rewrites() {
        const api = process.env.API_ORIGIN || "http://localhost:8000";
        return [
          { source: "/api/:path*", destination: `${api}/api/:path*` },
        ];
      },
    }
  : {
      output: "export",
      trailingSlash: true,
      images: { unoptimized: true },
    };

// Wrap the Next config with Sentry's build-time wrapper.
//
// `withSentryConfig` handles: source map upload (when SENTRY_AUTH_TOKEN is
// present), tree-shaking of Sentry logging code, and client bundle file
// widening. When SENTRY_AUTH_TOKEN is unset (local dev), source map upload is
// automatically skipped. Sentry init itself is gated on the DSN being set in
// the sentry.{client,server,edge}.config.ts files, so an empty DSN makes all
// Sentry calls no-op without blocking the build or app startup.
export default withSentryConfig(nextConfig, {
  // Suppress noisy build-time Sentry logs.
  silent: true,
  // Widen the set of files uploaded as part of the client bundle.
  widenClientFileUpload: true,
  // Tree-shake Sentry SDK logger statements from the bundle (v10 non-deprecated
  // form; equivalent to the old top-level `disableLogger: true`).
  webpack: {
    treeshake: {
      removeDebugLogging: true,
    },
  },
  // Source map upload config. Upload is automatically skipped when
  // SENTRY_AUTH_TOKEN is unset (e.g. local dev). By default, source maps are
  // deleted from the build output after upload (deleteSourcemapsAfterUpload
  // defaults to true), which hides them from browser devtools.
  org: process.env.SENTRY_ORG,
  project: process.env.SENTRY_PROJECT,
  authToken: process.env.SENTRY_AUTH_TOKEN,
});
