import type { NextConfig } from "next";
import { API_BASE, DEMO_WORKSPACE } from "./lib/env";

const mock = process.env.NEXT_PUBLIC_API_MOCK === "1";
const dev = process.env.NODE_ENV === "development";
const apiOrigin = new URL(API_BASE).origin;

// no nonces: pages are prerendered, so next's inline bootstrap and the theme script need 'unsafe-inline'; dev adds eval and the hmr socket
const contentSecurityPolicy = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${dev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob: https://i.ytimg.com https://*.ggpht.com https://*.googleusercontent.com",
  "font-src 'self'",
  `connect-src 'self' ${apiOrigin}${dev ? " ws: wss:" : ""}`,
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  ...(apiOrigin.startsWith("https:") ? ["upgrade-insecure-requests"] : []),
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: contentSecurityPolicy },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  ...(dev ? [] : [{ key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" }]),
];

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  experimental: {
    // barrel packages: load only the icons, primitives and motion parts actually imported
    optimizePackageImports: ["@remixicon/react", "react-aria-components", "motion"],
  },
  poweredByHeader: false,
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
  async redirects() {
    return [{ source: "/", destination: `/w/${DEMO_WORKSPACE}/clients`, permanent: false }];
  },
  turbopack: {
    // real builds swap the fixture api for a stub, so demo data isn't shipped
    resolveAlias: mock ? {} : { "./mock/mock-api": "./lib/api/mock/disabled.ts", "@/lib/api/mock/runs": "./lib/api/mock/disabled.ts" },
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
