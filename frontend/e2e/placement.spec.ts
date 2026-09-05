import { expect, test, type Page } from "@playwright/test";

import { letterboxdExport } from "./export";
import { signUpOwner } from "./owner";

const HEAT = 949;

test("an owner watches films, places them, and reads the ordering and the queue back", async ({
  page,
  request,
}) => {
  await signUpOwner(page, request, "place");

  // The first film has nothing to compare against, so it lands unasked.
  await markWatched(page, "Fight Club", "Rate now");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Fight Club landed");
  await expect(page.getByText("Number 1 of 1")).toBeVisible();
  await page.getByRole("button", { name: "Done" }).click();
  await expect(page).toHaveURL(/\/rated$/);

  // The second one gets the comparison. No undo, and nothing rating-shaped on screen.
  await markWatched(page, "Arrival", "Rate now");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Which did you like more?");
  await expect(page.getByRole("button", { name: "They're tied" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Skip this pair" })).toBeVisible();
  await expect(page.getByRole("button", { name: /undo/i })).toHaveCount(0);
  await page.getByRole("button", { name: "Arrival is better" }).click();

  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Arrival landed");
  await expect(page.getByText("Number 1 of 2")).toBeVisible();
  await page.getByRole("button", { name: "Done" }).click();

  const ordering = page.getByRole("region", { name: "Your ordering" }).getByRole("listitem");
  await expect(ordering.nth(0)).toContainText("Arrival");
  await expect(ordering.nth(1)).toContainText("Fight Club");

  // The third is rated later, so it waits in the queue until the owner leaves it there.
  await markWatched(page, "Heat", "Later");
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Rated" }).click();
  const queued = page.getByRole("listitem").filter({ hasText: "Heat" });
  await expect(queued).toBeVisible();
  await queued.getByRole("button", { name: "Not rating this one" }).click();
  await expect(page.getByText("Nothing waiting to be rated.")).toBeVisible();

  // Leaving the queue never touches watched-ness: the film is still seen.
  await page.goto(`/films/${HEAT}`);
  await expect(page.getByText("Watched, not rated")).toBeVisible();
  await expect(page.getByText("Waiting in your rate-later queue.")).toHaveCount(0);
});

/**
 * The tie on the wall: films the owner called equal share one rank, and each one still
 * gets its own cell.
 *
 * The wall is one grid of same-sized posters, so a tie is drawn onto its members rather
 * than boxed around them - every member carries the shared rank, marked shared. Geometry
 * is a by-eye check; what belongs here is that the accessible list did not collapse two
 * films into one item to draw them together.
 */
test("films the owner called equal share one rank and keep their own place on the wall", async ({
  page,
  request,
}) => {
  await signUpOwner(page, request, "tie");

  await markWatched(page, "Fight Club", "Rate now");
  await page.getByRole("button", { name: "Done" }).click();

  await markWatched(page, "Arrival", "Rate now");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Which did you like more?");
  await page.getByRole("button", { name: "They're tied" }).click();
  await page.getByRole("button", { name: "Done" }).click();

  const ordering = page.getByRole("region", { name: "Your ordering" }).getByRole("listitem");
  await expect(ordering).toHaveCount(2);
  await expect(ordering.filter({ hasText: "Fight Club" })).toContainText("=1");
  await expect(ordering.filter({ hasText: "Arrival" })).toContainText("=1");
});

/** Search for a film and log the watch, taking the rate-now-or-later offer. */
async function markWatched(page: Page, title: string, choice: "Rate now" | "Later"): Promise<void> {
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Search" }).click();
  await page.getByLabel("Find a film").fill(title);
  await page.getByRole("button", { name: "Search" }).click();
  const row = page.getByRole("listitem").filter({ hasText: title });
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "Mark watched" }).click();
  await row.getByRole("button", { name: choice, exact: true }).click();
  // "Rate now" leaves for the placement flow, which the caller waits on. "Later" stays
  // here, so wait for the row to flag the film watched before navigating away - or a
  // slower runner reads the rate-later queue before the watch has landed in it.
  if (choice === "Later") await expect(row.getByText("Watched, not rated")).toBeVisible();
}

/**
 * Settling one film, from the mark on the wall that opened it.
 *
 * The one journey the settling door gets: an imported library where every position is a
 * placeholder, the owner picking one film off the wall, answering it through, and the
 * mark being gone when they come back. Wiring, not behaviour - the head start and the
 * trust it earns are pinned at the API seam - but nothing below the browser can show
 * that the mark is reachable, opens the placement flow, and stops being there after.
 */
test("an owner settles one imported film from its mark and finds the mark gone", async ({
  page,
  request,
}) => {
  await signUpOwner(page, request, "settle");

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
      [],
    ),
  });
  await page.getByRole("button", { name: "Import your export" }).click();
  await expect(page.getByText("Every row found its film.")).toBeVisible({ timeout: 60_000 });

  // Every seeded position is a placeholder, so every film wears the mark.
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Rated" }).click();
  const mark = page
    .getByRole("listitem")
    .filter({ hasText: "Arrival" })
    .getByRole("button", { name: "Settle Arrival now" });
  await expect(mark).toBeVisible();

  // The mark is the door: it opens the placement flow on that film alone.
  await mark.click();
  await answerUntilLanded(page, "Arrival");

  // And the mark is gone, because the film's own answers now pin it.
  await page.getByRole("button", { name: "Done" }).click();
  await expect(page).toHaveURL(/\/rated$/);
  const settled = page.getByRole("listitem").filter({ hasText: "Arrival" });
  await expect(settled).toBeVisible();
  await expect(settled.getByText("settling")).toHaveCount(0);
});

/**
 * Answer whatever the flow asks, taking the first option offered, until the film lands.
 *
 * How many questions a settle takes is the API seam's business and depends on what the
 * film has already collected, so this answers until the done screen rather than a fixed
 * number of times. Each click waits for its own response, so the next turn of the loop
 * reads the step that answer produced rather than the one it replaced.
 */
async function answerUntilLanded(page: Page, title: string): Promise<void> {
  const landed = page.getByRole("heading", { level: 1, name: `${title} landed` });
  const answers = page.getByRole("button", { name: /is better$|is a \d/ });
  for (let step = 0; step < 8 && !(await landed.isVisible()); step += 1) {
    await Promise.all([
      page.waitForResponse(
        (response) =>
          response.url().includes("/api/placements/") && response.request().method() === "POST",
      ),
      answers.first().click(),
    ]);
    // The response lands a beat before the screen redraws, and every control is disabled
    // while the answer is in flight - so wait for the next step to be answerable rather
    // than clicking the button this answer is about to replace.
    await expect(page.getByRole("button", { disabled: true })).toHaveCount(0);
  }
  await expect(landed).toBeVisible();
}
