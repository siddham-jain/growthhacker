import type { NextConfig } from "next";
import { DEMO_WORKSPACE } from "./lib/env";

const mock = process.env.NEXT_PUBLIC_API_MOCK === "1";

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  experimental: {
    // barrel packages: load only the icons, primitives and motion parts actually imported
    optimizePackageImports: ["@remixicon/react", "react-aria-components", "motion"],
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
