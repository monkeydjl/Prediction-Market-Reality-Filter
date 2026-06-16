import type { NextConfig } from "next";

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

export default nextConfig;
