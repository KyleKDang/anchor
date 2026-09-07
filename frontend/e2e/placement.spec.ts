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
  // The way on from a landing is the wall in edit mode, with the landed film ringed.
  await page.getByRole("button", { name: "Adjust on the wall" }).click();
  await expect(page).toHaveURL(/\/rated\?edit=1&film=/);
  await expect(page.getByRole("button", { name: "Done editing" })).toBeVisible();
  await page.getByRole("button", { name: "Done editing" }).click();

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

  await page
    .getByRole("region", { name: "Your ordering" })
    .getByRole("link", { name: "Fight Club", exact: true })
    .click();
  await expect(page.getByRole("heading", { level: 2, name: "Your rating" })).toBeVisible();
  await page.getByRole("link", { name: "Re-rate it" }).click();

  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Rate it again");
  await expect(page.getByText("Currently")).toContainText("5.0");
  await pick(page, 2);

  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Fight Club landed");
  await page.getByRole("button", { name: "Adjust on the wall" }).click();
  // Edit mode draws every band as a drop target; the read-only wall draws the one that holds a film.
  await page.getByRole("button", { name: "Done editing" }).click();
  const rows = page.getByRole("region", { name: "Your ordering" }).getByRole("region");
  await expect(rows).toHaveCount(1);
  await expect(rows.nth(0)).toContainText("Fight Club");
  await expect(rows.nth(0)).toHaveAccessibleName("2.0 stars");
});

/**
 * The range: two bands the owner cannot choose between, narrowed by the comparisons.
 *
 * The journey the ticket names, end to end through the browser: select 5.0 and 4.5, lose
 * to the 5.0 anchor, beat the 4.5 one, and land at the seam the boundary question settles.
 * The rules underneath are pinned at the API seam; what this proves is that every door in
 * the flow is reachable and that the film comes to rest beside the film it was measured
 * against.
 */
test("an owner unsure between two bands narrows the range and lands at the seam", async ({
  page,
  request,
}) => {
  await signUpOwner(page, request, "range");

  // One film in each of the two bands, both marked as anchors, so each band has a
  // reference to ask about and a seam film to show at the boundary.
  await markWatched(page, "Fight Club", "Rate now");
  await pick(page, 5);
  await page.getByRole("link", { name: "Leave it where it is" }).click();
  await markWatched(page, "Arrival", "Rate now");
  await pick(page, 4.5);
  await page.getByRole("link", { name: "Leave it where it is" }).click();
  for (const title of ["Fight Club", "Arrival"]) {
    await page
      .getByRole("region", { name: "Your ordering" })
      .getByRole("link", { name: title, exact: true })
      .click();
    await page.getByRole("button", { name: "Mark as an anchor" }).click();
    await expect(page.getByRole("button", { name: "Retire this anchor" })).toBeVisible();
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Rated" }).click();
  }

  await markWatched(page, "Heat", "Rate now");
  await page.getByRole("button", { name: "Torn between two?" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Which are you between?");
  for (const band of [5, 4.5]) await select(page, band);
  await page.getByRole("button", { name: "Narrow it down" }).click();

  // Worse than the weakest 5.0 anchor, and better than the strongest 4.5 one: the two
  // answers anchors cannot settle, because an anchor bounds and never floors.
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("How does it compare?");
  await page.getByRole("button", { name: "Fight Club is better" }).click();
  await page.getByRole("button", { name: "Heat is better" }).click();

  // The boundary question: the two films either side of the line, and which it belongs next to.
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Which is it closer to?");
  await page.getByRole("button").filter({ hasText: "Arrival" }).click();

  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Heat landed");
  await expect(page.getByText("Number 1 of 2")).toBeVisible();
  await page.getByRole("button", { name: "Adjust on the wall" }).click();
  const banded = page.getByRole("region", { name: "4.5 stars" });
  await expect(banded.getByRole("listitem").nth(0)).toContainText("Heat");
  await expect(banded.getByRole("listitem").nth(1)).toContainText("Arrival");
});

/** Add a band to the range being selected. The row is a toggle in this mode, not a pick. */
async function select(page: Page, band: number): Promise<void> {
  await page
    .getByRole("list", { name: "Choose a range" })
    .getByRole("button")
    .filter({ hasText: `${band.toFixed(1)}` })
    .first()
    .click();
}

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
