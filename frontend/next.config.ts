import type { NextConfig } from "next";

/**
 * Presentation tier config.
 * The UI is an API-driven dashboard: every page is a client component that
 * talks to the FastAPI application tier (NEXT_PUBLIC_API_URL). No server-side
 * data caching is needed, so Next's cache-components features stay off.
 */
const nextConfig: NextConfig = {
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
