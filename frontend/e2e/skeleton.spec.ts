import { expect, test } from "@playwright/test";

test("a visitor without a session is sent to the login screen on a stack whose health check crosses web, database, and worker", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Log in");

  const health = await request.get("/api/health");
  expect(health.status()).toBe(200);
  expect(await health.json()).toEqual({
    status: "ok",
    checks: { web: "ok", database: "ok", worker: "ok" },
  });
});
