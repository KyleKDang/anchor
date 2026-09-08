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
  const body = await health.json();
  expect(body.status).toBe("ok");
  expect(body.checks).toEqual({ web: "ok", database: "ok", worker: "ok" });
  // The backlog rides alongside the checks and never gates anything, so this asserts its
  // shape and not its depth: the other journeys run in parallel, and their imports are
  // exactly the queued work it exists to report (#82).
  expect(body.backlog).toEqual({
    waiting: expect.any(Number),
    oldest_wait_seconds: body.backlog.waiting === 0 ? null : expect.any(Number),
  });
  // The compose stack runs keyless on purpose, so this is the one place the missing
  // answer is proved against a real container rather than a test client - and proved
  // not to gate the deploy, since --wait reads this endpoint (#109).
  expect(body.llm_credential).toBe("missing");
});
