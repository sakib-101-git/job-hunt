import { cloudflareTest } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

// Wrangler reads `secrets.required` values from the environment. Tests get a
// fixed fake value so they never depend on a local .dev.vars file.
process.env.TELEGRAM_WEBHOOK_SECRET = "test-webhook-secret-0123456789";

export default defineConfig({
  plugins: [cloudflareTest({ wrangler: { configPath: "./wrangler.toml" } })],
});
