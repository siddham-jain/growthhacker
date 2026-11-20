// regenerates lib/api/schema.d.ts from the backend's committed openapi.json
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";

const spec = new URL("../lib/api/openapi.json", import.meta.url).pathname;
if (!existsSync(spec)) {
  console.error("lib/api/openapi.json is missing. Run: cd backend && uv run python -m app.api.cli openapi --out ../frontend/lib/api/openapi.json");
  process.exit(1);
}
execFileSync("pnpm", ["exec", "openapi-typescript", spec, "-o", "lib/api/schema.d.ts"], { stdio: "inherit" });
