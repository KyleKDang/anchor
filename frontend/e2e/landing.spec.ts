import { expect, test } from "@playwright/test";

import { signUpOwner } from "./owner";

/**
 * The front door, and the one route with two faces.
 *
 * Selected by role, label and text throughout: the page is composed rather than
 * inherited and its visual direction is expected to be revisited, so a journey that
 * reached for a class would break on the first restyle (#113).
 */
test("a signed-out visitor lands on the front door and can reach both auth screens, while a signed-in owner is still sent into the app", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "Rank every film you've ever seen.",
  );

  // What the page claims Anchor is, and the three surfaces it shows exist.
  await expect(
    page.getByText("A tier list of everything you've watched", { exact: false }),
  ).toBeVisible();
  for (const claim of [
    "Everything you've watched, ranked by hand.",
    "A watchlist that ranks itself.",
    "Recommendations that know your taste.",
  ]) {
    await expect(page.getByRole("heading", { name: claim })).toBeVisible();
  }

  // The attribution TMDB's terms require wherever their art is shown (ADR 0003).
  await expect(page.getByRole("img", { name: "The Movie Database (TMDB)" })).toBeVisible();
  await expect(
    page.getByText("not endorsed, certified, or otherwise approved by TMDB", { exact: false }),
  ).toBeVisible();

  // The demo lands with #108; until then nothing on the page offers one.
  await expect(page.getByRole("link", { name: /demo/i })).toHaveCount(0);

  // Sign up is the primary call to action, and it is the first thing in the hero's row.
  await page.getByRole("main").getByRole("link", { name: "Sign up" }).first().click();
  await expect(page).toHaveURL(/\/signup$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Create your account");

  await page.goto("/");
  await page.getByRole("main").getByRole("link", { name: "Log in" }).first().click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Log in");

  // The rail carries the same two verbs.
  await page.goto("/");
  await page.getByRole("navigation", { name: "Account" }).first().getByText("Log in").click();
  await expect(page).toHaveURL(/\/login$/);

  // Signed in, the root is a redirect into the app exactly as it was before the landing
  // page existed: the account's entry fork, and then its first destination.
  await signUpOwner(page, request, "landing");
  await page.goto("/");
  await expect(page).toHaveURL(/\/watchlist$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Watchlist");
});
