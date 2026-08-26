import { defineConfig } from "@playwright/test";

/** The browser smoke suite: a handful of journeys over the full running stack. */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: process.env.ANCHOR_BASE_URL ?? "http://localhost",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
