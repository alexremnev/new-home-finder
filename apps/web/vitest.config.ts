import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

// The one thing the tests need that Next provides and vitest does not: the "@"
// alias from tsconfig. Without it a module that imports "@/lib/db" cannot be
// loaded from a test at all, which quietly made a third of lib/ untestable.
export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL(".", import.meta.url)),
    },
  },
});
