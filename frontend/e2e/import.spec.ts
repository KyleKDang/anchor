import { expect, test } from "@playwright/test";

import { letterboxdExport } from "./export";
import { signUpOwner } from "./owner";

/**
 * The seed import across the whole stack: the web process reads the upload, the worker
 * matches every row against TMDB, and the account comes out with a library.
 *
 * Wiring, not behaviour - the matcher's own rules are pinned at the API seam. What only
 * a running stack can show is that the upload reaches the queue, the worker picks the
 * job up, and what it writes is on the screens by the time the owner looks.
 */
test("an owner imports a Letterboxd export and finds their ratings and watchlist waiting", async ({
  page,
  request,
}) => {
  await signUpOwner(page, request, "import");

  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Profile" }).click();
  await page.getByLabel("Your Letterboxd export (.zip)").setInputFiles({
    name: "letterboxd-owner-2026-08-02-11-00-utc.zip",
    mimeType: "application/zip",
    buffer: letterboxdExport(
      [
        { name: "Fight Club", year: 1999, rating: 5 },
        { name: "Arrival", year: 2016, rating: 4 },
        { name: "Heat", year: 1995, rating: 3 },
      ],
      [{ name: "Parasite", year: 2019 }],
    ),
  });
  await page.getByRole("button", { name: "Import your export" }).click();

  // Matching is a background job, and the area polls until it is done. Generous: the
  // worker has to pick the job up and then talk to TMDB once per row.
  await expect(page.getByText("Every row found its film.")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("3 ratings, 1 watchlist film, 3 watched films")).toBeVisible();

  // The wall came out in band rows, showing the half-stars the owner already knows.
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Rated" }).click();
  await expect(page.getByRole("heading", { name: "5.0 stars" })).toBeVisible();
  const ordering = page.getByRole("listitem").filter({ hasText: "Fight Club" });
  await expect(ordering.first()).toBeVisible();
  // Nothing is provisional: an imported row is rated and final the moment it is matched.
  await expect(page.getByText("settling")).toHaveCount(0);

  // The watchlist row seeded the backlog; the films rated in the same import did not.
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Watchlist" }).click();
  await expect(page.getByRole("listitem").filter({ hasText: "Parasite" })).toBeVisible();
  await expect(page.getByRole("listitem").filter({ hasText: "Fight Club" })).toHaveCount(0);
});

/**
 * The account that was not empty when its export arrived.
 *
 * An owner who tries Anchor before exporting holds films no import put there, and the
 * import erases them like any other reset - there is no merge path, ever. The screen has
 * to say so before the upload rather than after, and the wipe has to actually happen.
 */
test("an owner who already started the account is told what importing will erase", async ({
  page,
  request,
}) => {
  await signUpOwner(page, request, "import-reset");

  // Added by hand, with no export anywhere in sight: the account is not empty.
  expect((await page.request.post("/api/films/244786/backlog")).ok()).toBeTruthy();

  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Profile" }).click();
  await expect(
    page.getByRole("heading", { name: "You have already started this account" }),
  ).toBeVisible();
  await expect(page.getByText("This erases 1 backlog film")).toBeVisible();

  await page.getByLabel("Your Letterboxd export (.zip)").setInputFiles({
    name: "letterboxd-owner-2026-08-02-11-00-utc.zip",
    mimeType: "application/zip",
    buffer: letterboxdExport([{ name: "Fight Club", year: 1999, rating: 5 }], []),
  });
  await page.getByRole("button", { name: "Erase and import" }).click();
  await expect(page.getByText("Every row found its film.")).toBeVisible({ timeout: 60_000 });

  // Whiplash was hand-added and the new export never named it, so the reset took it.
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Watchlist" }).click();
  await expect(page.getByRole("listitem").filter({ hasText: "Whiplash" })).toHaveCount(0);
});

/**
 * The replace control waits for the import it would replace.
 *
 * Mid-match the account is half built, so the enumerated counts climb under a
 * destructive button and read as counting the wrong thing - they are true at every
 * tick, which is precisely why a moving one is misleading. The review queue already
 * waits for the same reason; this pins the reset alongside it.
 *
 * The real matching window is milliseconds against a fake TMDB, so the status is held
 * at "matching" from the test rather than raced for.
 */
test("the replace-everything control is held back until matching is done", async ({
  page,
  request,
}) => {
  await signUpOwner(page, request, "import-matching");

  // Off until the export is in: the upload screen only shows while the status is "none".
  let held = false;
  await page.route("**/api/import", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    const response = await route.fetch();
    const state = await response.json();
    await route.fulfill({
      response,
      json: held ? { ...state, status: "matching", pending: 2 } : state,
    });
  });

  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Profile" }).click();
  await page.getByLabel("Your Letterboxd export (.zip)").setInputFiles({
    name: "letterboxd-owner-2026-08-02-11-00-utc.zip",
    mimeType: "application/zip",
    buffer: letterboxdExport([{ name: "Heat", year: 1995, rating: 3 }], []),
  });
  held = true;
  await page.getByRole("button", { name: "Import your export" }).click();

  // Matching says its piece; the reset says nothing at all until there is a settled
  // account to enumerate.
  await expect(page.getByText("Matching against TMDB")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Replace with a new export" })).toHaveCount(0);

  // And it comes back the moment the import lands, counting what actually settled.
  held = false;
  await expect(page.getByRole("heading", { name: "Replace with a new export" })).toBeVisible({
    timeout: 60_000,
  });
  await expect(page.getByText("This erases 1 rating")).toBeVisible();
});
