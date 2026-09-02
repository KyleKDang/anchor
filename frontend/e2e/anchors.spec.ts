import { expect, test, type Page } from "@playwright/test";

import { signUpOwner } from "./owner";

const ARRIVAL = 329865;

test("an owner designates a band anchor and the Rated screen groups the ordering by band", async ({
  page,
  request,
}) => {
  await signUpOwner(page, request, "anchor");

  // A fresh ordering has no band structure at all, so it says so rather than
  // inventing stars, and the nudge explains where they have gone.
  // The first film has nothing to compare against, so it lands unasked; the second
  // takes one comparison, and losing it puts it underneath.
  await markWatched(page, "Arrival");
  await page.getByRole("button", { name: "Done" }).click();
  await markWatched(page, "Fight Club");
  await page.getByRole("button", { name: "Arrival is better" }).click();
  await page.getByRole("button", { name: "Done" }).click();
  await expect(page.getByText("Rating pending").first()).toBeVisible();
  await expect(page.getByRole("link", { name: /^Start with/ })).toBeVisible();

  // Designating is the one place the owner assigns a band directly, and it is what
  // erects the first dividers - so the film's stars materialize on the way back.
  await page.goto(`/films/${ARRIVAL}`);
  await page.getByLabel("Make this my canonical…").selectOption("4");
  await page.getByRole("button", { name: "Designate" }).click();
  await expect(page.getByText("Anchor", { exact: true })).toBeVisible();

  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Rated" }).click();
  const banded = page.getByRole("region", { name: "4.0 stars" });
  await expect(banded.getByRole("heading", { level: 3 })).toContainText("4.0");
  await expect(banded.getByRole("listitem")).toContainText("Arrival");
  await expect(page.getByRole("link", { name: /^Start with/ })).toHaveCount(0);

  // Everything the dividers cannot yet decide stays honestly unrated.
  await expect(page.getByRole("region", { name: "Rating pending" })).toContainText("Fight Club");
});

/** Search for a film and take the rate-now branch into the placement flow. */
async function markWatched(page: Page, title: string): Promise<void> {
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Search" }).click();
  await page.getByLabel("Find a film").fill(title);
  await page.getByRole("button", { name: "Search" }).click();
  const row = page.getByRole("listitem").filter({ hasText: title });
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "Mark watched" }).click();
  await row.getByRole("button", { name: "Rate now", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
}
