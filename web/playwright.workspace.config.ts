import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "workspace-frame.spec.ts",
  use: {
    baseURL: "http://127.0.0.1:8767",
    browserName: "chromium",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "pnpm exec vite --host 127.0.0.1 --port 8767 --strictPort",
    url: "http://127.0.0.1:8767/e2e/fixtures/workspace/index.html",
    reuseExistingServer: false,
    gracefulShutdown: { signal: "SIGTERM", timeout: 10000 },
  },
});
