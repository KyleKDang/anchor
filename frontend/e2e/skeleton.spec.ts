import { expect, test } from "@playwright/test";

test("a visitor without a session gets the landing page on a stack whose health check crosses web, database, and worker", async ({
  page,
  request,
}) => {
  // The root has painted a front door rather than bounced to the login card since #113;
  // what this line is here for is unchanged - the bundle boots and the session read
  // came back. Where "/" leads for a visitor and for an owner is landing.spec.ts.
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "Rank every film you've ever seen.",
  );

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
  // Shape, not value: this journey runs twice, against the keyless compose stack where
  // the answer is "missing" and against production where it is "configured". Both are
  // healthy, which is the claim - reporting the credential never gates the stack, and
  // --wait reads this endpoint (#109).
  expect(["configured", "missing"]).toContain(body.llm_credential);
});
