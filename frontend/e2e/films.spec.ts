import { expect, test } from "@playwright/test";

import { signUpOwner } from "./owner";

test("an owner searches, adds a film to the backlog, opens it, marks it watched, and sees the TMDB attribution", async ({
  page,
  request,
}) => {
  await signUpOwner(page, request, "films");

  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Search" }).click();
  await page.getByLabel("Find a film").fill("Fight Club");
  await page.getByRole("button", { name: "Search" }).click();
  const result = page.getByRole("listitem").filter({ hasText: "Fight Club" });
  await expect(result).toBeVisible();
  await result.getByRole("button", { name: "Add to backlog" }).click();
  await expect(result.getByText("In your backlog")).toBeVisible();

  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Watchlist" }).click();
  const row = page.getByRole("listitem").filter({ hasText: "Fight Club" });
  await expect(row).toBeVisible();

  await row.getByRole("link", { name: "Fight Club" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Fight Club");
  await expect(page.getByText("In your backlog")).toBeVisible();
  // The plot sits behind the spoiler toggle until the owner asks for it.
  await expect(page.getByText("ending and all")).toBeHidden();
  await page.locator("details.spoiler > summary").click();
  await expect(page.getByText("ending and all")).toBeVisible();
  // Logging a watch is always a choice; "later" seats the film in the rate-later queue.
  await page.getByRole("button", { name: "I watched this" }).click();
  await page.getByRole("button", { name: "Later", exact: true }).click();
  await expect(page.getByText("Waiting in your rate-later queue.")).toBeVisible();

  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Watchlist" }).click();
  await expect(page.getByRole("listitem").filter({ hasText: "Fight Club" })).toHaveCount(0);

  // TMDB's terms require both halves of this, on a screen the owner can reach (ADR 0003).
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Profile" }).click();
  await expect(page.getByAltText("The Movie Database (TMDB)")).toBeVisible();
  // The logo file has to actually be there. A broken <img> still renders its alt text,
  // and the SPA fallback answers any unknown path with index.html, so only the content
  // type tells a real asset from a missing one.
  const logo = await request.get("/tmdb.svg");
  expect(logo.headers()["content-type"], "frontend/public/tmdb.svg is missing").toContain("svg");
  await expect(
    page.getByText("not endorsed, certified, or otherwise approved by TMDB"),
  ).toBeVisible();
});
