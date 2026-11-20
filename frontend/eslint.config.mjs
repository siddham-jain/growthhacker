import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    // feature code reaches vendored kits only through components/ui, so swapping a kit touches one wrapper
    files: ["app/**/*.{ts,tsx}", "components/**/*.{ts,tsx}", "lib/**/*.{ts,tsx}"],
    ignores: ["components/ui/**", "components/kits/**"],
    rules: {
      "no-restricted-imports": ["error", { patterns: [{ group: ["@/components/kits/*"], message: "Import kit components through @/components/ui." }] }],
    },
  },
  {
    // vendored registry sources stay as upstream wrote them; react compiler rules are enforced on our code only
    files: ["components/kits/**/*.{ts,tsx}", "hooks/**/*.ts"],
    rules: {
      "react-hooks/set-state-in-effect": "off",
      "react-hooks/immutability": "off",
      "react-hooks/exhaustive-deps": "off",
      "@next/next/no-img-element": "off",
    },
  },
  globalIgnores([".next/**", "out/**", "build/**", "next-env.d.ts"]),
]);

export default eslintConfig;
