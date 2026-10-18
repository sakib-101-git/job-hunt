import type { D1Migration } from "cloudflare:test";

// Bindings that exist only in tests (see vitest.config.ts).
declare global {
  namespace Cloudflare {
    interface Env {
      TEST_MIGRATIONS: D1Migration[];
    }
  }
}
