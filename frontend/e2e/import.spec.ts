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

  // The ordering came out in band order, showing the half-stars the owner already knows.
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Rated" }).click();
  await expect(page.getByRole("heading", { name: "5.0 stars" })).toBeVisible();
  const ordering = page.getByRole("listitem").filter({ hasText: "Fight Club" });
  await expect(ordering.first()).toBeVisible();
  // Seeded placements are trusted less than compared ones, and say so.
  await expect(page.getByText("settling").first()).toBeVisible();

  // The watchlist row seeded the backlog; the films rated in the same import did not.
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Watchlist" }).click();
  await expect(page.getByRole("listitem").filter({ hasText: "Parasite" })).toBeVisible();
  await expect(page.getByRole("listitem").filter({ hasText: "Fight Club" })).toHaveCount(0);
});
