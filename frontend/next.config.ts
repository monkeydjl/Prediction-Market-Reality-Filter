import type { NextConfig } from "next";

const isDev = process.env.NODE_ENV === "development";

// Dev: a normal Next server at `/` with API rewrites proxied to FastAPI.
// Prod (`next build`): static export into out/ under /app, served by FastAPI.
// `output: export` disables rewrites, so it must NOT be set in dev.
const nextConfig: NextConfig = isDev
  ? {
      images: { unoptimized: true },
      async rewrites() {
        const api = process.env.API_ORIGIN || "http://localhost:8000";
        return [
          { source: "/events/:path*", destination: `${api}/events/:path*` },
          { source: "/calibration/:path*", destination: `${api}/calibration/:path*` },
        ];
      },
    }
  : {
      output: "export",
      basePath: "/app",
      assetPrefix: "/app",
      trailingSlash: true,
      images: { unoptimized: true },
    };

export default nextConfig;
