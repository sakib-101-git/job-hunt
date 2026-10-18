import path from "node:path";

import { cloudflareTest, readD1Migrations } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

// Wrangler reads `secrets.required` values from the environment. Tests get a
// fixed fake value so they never depend on a local .dev.vars file.
process.env.TELEGRAM_WEBHOOK_SECRET = "test-webhook-secret-0123456789";

// The same migrations production uses, applied to the local test D1 by
// test/apply-migrations.ts.
const migrations = await readD1Migrations(path.join(import.meta.dirname, "..", "migrations"));

export default defineConfig({
  plugins: [
    cloudflareTest({
      wrangler: { configPath: "./wrangler.toml" },
      miniflare: { bindings: { TEST_MIGRATIONS: migrations } },
    }),
  ],
  test: { setupFiles: ["./test/apply-migrations.ts"] },
});
