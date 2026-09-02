import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

import { verificationPath } from "./mail";

test("an owner watches two films, places them, and reads the ordering back", async ({
  page,
  request,
}) => {
  await signUp(page, request, "place");

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

  const ordering = page.getByRole("list").first().getByRole("listitem");
  await expect(ordering.nth(0)).toContainText("Arrival");
  await expect(ordering.nth(1)).toContainText("Fight Club");
});

test("an owner rates a film later, then leaves the queue without unwatching it", async ({
  page,
  request,
}) => {
  await signUp(page, request, "later");

  await markWatched(page, "Heat", "Later");

  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Rated" }).click();
  const queued = page.getByRole("listitem").filter({ hasText: "Heat" });
  await expect(queued).toBeVisible();
  await queued.getByRole("button", { name: "Not rating this one" }).click();
  await expect(page.getByText("Nothing waiting to be rated.")).toBeVisible();

  // Leaving the queue never touches watched-ness: the film is still seen.
  await page.goto("/films/949");
  await expect(page.getByText("Watched, not rated")).toBeVisible();
  await expect(page.getByText("Waiting in your rate-later queue.")).toHaveCount(0);
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
}

async function signUp(page: Page, request: APIRequestContext, prefix: string): Promise<void> {
  const email = `${prefix}-${Date.now()}@example.com`;
  const password = "correct horse battery staple";
  await page.goto("/signup");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign up" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Check your email");
  await page.goto(await verificationPath(request, email));
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Verify and log in" }).click();
  await expect(page).toHaveURL(/\/watchlist$/);
}
