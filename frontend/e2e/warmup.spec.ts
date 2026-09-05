import { expect, test, type Page } from "@playwright/test";

import { verificationPath } from "./mail";
import { PASSWORD } from "./owner";

/**
 * A fresh account, from signing up to a usable one, through the whole of onboarding.
 *
 * The one journey that does not use the shared sign-up helper, because the helper's job
 * is to get past the entry fork and this is the journey that walks through it. It covers
 * the wiring rather than the behaviour, per testing.md: that the fork leads somewhere,
 * that designating from search runs the placement flow and comes back, that the evidence
 * and backlog phases work, and that the app is usable at the end of it.
 */
test("a fresh owner takes the entry fork, warms up, and comes out with a usable account", async ({
  page,
  request,
}) => {
  const email = `warmup-${Date.now()}@example.com`;
  await page.goto("/signup");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign up" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Check your email");
  await page.goto(await verificationPath(request, email));
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Verify and log in" }).click();

  // The entry fork is where a brand-new account lands, and it offers both ways in.
  await expect(page).toHaveURL(/\/welcome$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Let's find your scale");
  await expect(page.getByRole("button", { name: "Import my export" })).toBeVisible();
  await page.getByRole("button", { name: "Start fresh" }).click();
  await expect(page).toHaveURL(/\/warmup$/);

  // Phase 1. Search leads; the grid is behind a question the owner has to ask.
  await expect(page.getByRole("heading", { name: /definitive 5\.0/ })).toBeVisible();
  await page.getByRole("button", { name: "Browse popular" }).click();
  await expect(page.getByRole("button", { name: "This is my 5.0" }).first()).toBeVisible();

  await designate(page, "Fight Club", "5.0");
  // Designating a film nobody has placed runs the placement that decides it. The first
  // film has nothing to compare against, so it lands unasked.
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Fight Club landed");
  await expect(page.getByText("It landed in the band")).toBeVisible();
  await page.getByRole("button", { name: "Done" }).click();
  await expect(page).toHaveURL(/\/warmup$/);

  // The run moves on to the next band by itself, and one band can be put away alone.
  await expect(page.getByRole("heading", { name: /definitive 1\.0/ })).toBeVisible();
  await page.getByRole("button", { name: "Skip this band" }).click();
  await expect(page.getByRole("heading", { name: /definitive 3\.0/ })).toBeVisible();

  // Phase 2 on this fill is ordinary placements, so it points at Search and counts them.
  await expect(page.getByText(/0 of about 5 logged/)).toBeVisible();

  // Phase 3: the backlog, which is usable from the moment it holds anything.
  await page.getByLabel("Find something to watch").fill("Parasite");
  await page
    .getByRole("search")
    .filter({ has: page.getByLabel("Find something to watch") })
    .getByRole("button", { name: "Search" })
    .click();
  await page.getByRole("button", { name: "Add to backlog" }).first().click();
  await expect(page.getByText("1 film in your backlog")).toBeVisible();

  // Putting the warmup away leaves an account that works, with what the warmup built.
  await page.getByRole("button", { name: /I'm done for now|Take me in/ }).click();
  await expect(page).toHaveURL(/\/rated$/);
  await expect(page.getByRole("link", { name: "Fight Club" })).toBeVisible();
  // The stars are there because a band has an exemplar, which is what phase one was for.
  await expect(page.getByRole("heading", { name: /5\.0/ })).toBeVisible();

  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Watchlist" }).click();
  await expect(page.getByRole("link", { name: "Parasite" })).toBeVisible();

  // Dismissed is put away, not destroyed: Profile is where it stays reachable, along
  // with the import the fork offered on the other branch.
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Profile" }).click();
  await expect(page.getByRole("link", { name: "Pick up the warmup" })).toBeVisible();
  // Exact: this is the area's own heading, and the sync list inside it has one of its
  // own that says "Letterboxd" too - a warmed-up account has ratings Letterboxd never saw.
  await expect(page.getByRole("heading", { name: "Letterboxd", exact: true })).toBeVisible();
});

/** Search for a film inside the current band prompt and make it that band's anchor. */
async function designate(page: Page, title: string, band: string): Promise<void> {
  await page.getByLabel(`Find your ${band}`).fill(title);
  await page
    .getByRole("search")
    .filter({ has: page.getByLabel(`Find your ${band}`) })
    .getByRole("button", { name: "Search" })
    .click();
  await page
    .getByRole("listitem")
    .filter({ hasText: title })
    .getByRole("button", { name: `This is my ${band}` })
    .click();
}
