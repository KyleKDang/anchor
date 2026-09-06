import { expect, test, type Page } from "@playwright/test";

import { signUpOwner } from "./owner";

const HEAT = 949;

test("an owner watches films, rates them, and reads the wall and the queue back", async ({
  page,
  request,
}) => {
  await signUpOwner(page, request, "place");

  // One tap on the band picker is the whole of rating a film.
  await markWatched(page, "Fight Club", "Rate now");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("How was it?");
  await expect(page.getByRole("button", { name: /undo/i })).toHaveCount(0);
  await pick(page, 5);

  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Fight Club landed");
  await expect(page.getByText("Number 1 of 1")).toBeVisible();
  await page.getByRole("link", { name: "Leave it where it is" }).click();
  await expect(page).toHaveURL(/\/rated$/);

  // A second film in a different band: the wall grows a second row.
  await markWatched(page, "Arrival", "Rate now");
  await pick(page, 3);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Arrival landed");
  await page.getByRole("button", { name: "Adjust on the wall" }).click();
  await expect(page).toHaveURL(/\/rated\?film=/);

  // Best band first, with the half-star value as each row's header.
  const rows = page.getByRole("region", { name: "Your ordering" }).getByRole("region");
  await expect(rows.nth(0)).toContainText("Fight Club");
  await expect(rows.nth(1)).toContainText("Arrival");

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
 * Re-rating from a film's own page, which is the whole of correcting a rating today.
 *
 * The picker opens with the current band marked, a pick into another band moves the film,
 * and the wall shows it in its new row. Wiring, not behaviour - the rank rules are pinned
 * at the API seam - but nothing below the browser can show the door is reachable.
 */
test("an owner re-rates a film from its page and the wall follows", async ({ page, request }) => {
  await signUpOwner(page, request, "rerate");

  await markWatched(page, "Fight Club", "Rate now");
  await pick(page, 5);
  await page.getByRole("link", { name: "Leave it where it is" }).click();

  await page.getByRole("region", { name: "Your ordering" }).getByText("Fight Club").click();
  await expect(page.getByRole("heading", { level: 2, name: "Your rating" })).toBeVisible();
  await page.getByRole("link", { name: "Re-rate it" }).click();

  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Rate it again");
  await expect(page.getByText("Currently")).toContainText("5.0");
  await pick(page, 2);

  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Fight Club landed");
  await page.getByRole("button", { name: "Adjust on the wall" }).click();
  const rows = page.getByRole("region", { name: "Your ordering" }).getByRole("region");
  await expect(rows).toHaveCount(1);
  await expect(rows.nth(0)).toContainText("Fight Club");
  await expect(rows.nth(0)).toHaveAccessibleName("2.0 stars");
});

/** Tap a band on the picker. The row is the target, and its value is its name. */
async function pick(page: Page, band: number): Promise<void> {
  await page
    .getByRole("list", { name: "Pick a rating" })
    .getByRole("button")
    .filter({ hasText: `${band.toFixed(1)}` })
    .first()
    .click();
}

/** Search for a film and log the watch, taking the rate-now-or-later offer. */
async function markWatched(page: Page, title: string, choice: "Rate now" | "Later"): Promise<void> {
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Search" }).click();
  await page.getByLabel("Find a film").fill(title);
  await page.getByRole("button", { name: "Search" }).click();
  const row = page.getByRole("listitem").filter({ hasText: title });
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "Mark watched" }).click();
  await row.getByRole("button", { name: choice, exact: true }).click();
  // "Rate now" leaves for the picker, which the caller waits on. "Later" stays here, so
  // wait for the row to flag the film watched before navigating away - or a slower runner
  // reads the rate-later queue before the watch has landed in it.
  if (choice === "Later") await expect(row.getByText("Watched, not rated")).toBeVisible();
}
