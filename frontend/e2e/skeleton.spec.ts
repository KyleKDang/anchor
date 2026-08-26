import { expect, test } from "@playwright/test";

const destinations = ["Watchlist", "Discovery", "Rated", "Search", "Profile"];

test("a visitor walks the five destinations on a stack whose health check crosses web, database, and worker", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/watchlist$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Watchlist");

  for (const destination of destinations) {
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: destination }).click();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(destination);
  }

  const health = await request.get("/api/health");
  expect(health.status()).toBe(200);
  expect(await health.json()).toEqual({
    status: "ok",
    checks: { web: "ok", database: "ok", worker: "ok" },
  });
});
