import { expect, test, type Page } from "@playwright/test";

import { signUpOwner } from "./owner";

const ARRIVAL = 329865;

test("an owner marks an anchor and the wall badges it in its band row", async ({
  page,
  request,
}) => {
  await signUpOwner(page, request, "anchor");

  // Two films in two bands: the wall has its rows from the first rating onward, because
  // the band is the rating and the owner chose it.
  await rate(page, "Arrival", 4);
  await rate(page, "Fight Club", 2.5);
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Rated" }).click();
  await expect(page.getByRole("region", { name: "4.0 stars" })).toContainText("Arrival");
  await expect(page.getByRole("region", { name: "2.5 stars" })).toContainText("Fight Club");

  // No anchors yet, so the one ambient line says what marking one does - in edit mode,
  // which is where the toggle is on this screen; the read-only wall says nothing.
  await expect(page.getByText(/Marking a film an anchor/)).toHaveCount(0);
  await page.getByRole("button", { name: "Edit the wall" }).click();
  await expect(page.getByText(/Marking a film an anchor/)).toBeVisible();
  await page.getByRole("button", { name: "Done editing" }).click();

  // The toggle also lives on the film's own page, and marking changes nothing else.
  await page.goto(`/films/${ARRIVAL}`);
  await page.getByRole("button", { name: "Mark as an anchor" }).click();
  // Scoped to the film, not the page: the nav's wordmark is also the word "Anchor", and
  // an unscoped match is satisfied by it the instant the page renders.
  await expect(page.getByRole("article").getByText("Anchor", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retire this anchor" })).toBeVisible();

  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Rated" }).click();
  const banded = page.getByRole("region", { name: "4.0 stars" });
  await expect(banded.getByRole("heading", { level: 3 })).toContainText("4.0");
  await expect(banded).toContainText("1 anchor");
  await expect(banded.getByRole("listitem")).toContainText("Arrival");
  await page.getByRole("button", { name: "Edit the wall" }).click();
  await expect(page.getByText(/Marking a film an anchor/)).toHaveCount(0);
  await page.getByRole("button", { name: "Done editing" }).click();

  // The anchors-only filter is the one way the wall narrows to them.
  await page.getByLabel("Anchors only").check();
  const kept = page.getByRole("region", { name: "Your ordering" }).getByRole("listitem");
  await expect(kept).toHaveCount(1);
  await expect(kept.nth(0)).toContainText("Arrival");
});

/** Search for a film, log the watch, and tap a band on the picker. */
async function rate(page: Page, title: string, band: number): Promise<void> {
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Search" }).click();
  await page.getByLabel("Find a film").fill(title);
  await page.getByRole("button", { name: "Search" }).click();
  const row = page.getByRole("listitem").filter({ hasText: title });
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "Mark watched" }).click();
  await row.getByRole("button", { name: "Rate now", exact: true }).click();
  await page
    .getByRole("list", { name: "Pick a rating" })
    .getByRole("button")
    .filter({ hasText: `${band.toFixed(1)}` })
    .first()
    .click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(`${title} landed`);
  await page.getByRole("link", { name: "Leave it where it is" }).click();
}
