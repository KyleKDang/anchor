import { expect, test, type Page } from "@playwright/test";

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
