import { defineConfig, devices } from "@playwright/test";

declare const process: { env: Record<string, string | undefined> };

const baseURL = process.env.FIRE_INSPECT_BROWSER_BASE_URL?.replace(/\/$/, "");
if (!baseURL) throw new Error("FIRE_INSPECT_BROWSER_BASE_URL is required");
if (new URL(baseURL).protocol !== "https:") {
  throw new Error("browser acceptance requires an HTTPS base URL");
}

const executablePath = process.env.FIRE_INSPECT_BROWSER_EXECUTABLE;

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.e2e.ts",
  fullyParallel: false,
  workers: 1,
  forbidOnly: true,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  preserveOutput: "never",
  outputDir: "../test-results/browser-artifacts",
  reporter: [
    ["line"],
    ["junit", { outputFile: "../test-results/browser-e2e.xml", includeProjectInTestName: false }],
  ],
  use: {
    ...devices["Desktop Chrome"],
    baseURL,
    ignoreHTTPSErrors: true,
    serviceWorkers: "block",
    trace: "off",
    screenshot: "off",
    video: "off",
    launchOptions: executablePath ? { executablePath } : undefined,
    channel: executablePath ? undefined : "chrome",
  },
});
