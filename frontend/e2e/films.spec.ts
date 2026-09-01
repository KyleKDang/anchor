import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

import { verificationPath } from "./mail";

test("an owner searches, adds a film to the backlog, opens it, marks it watched, and sees the TMDB attribution", async ({
  page,
  request,
}) => {
  await signUp(page, request);

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
  await page.getByRole("button", { name: "I watched this" }).click();
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

async function signUp(page: Page, request: APIRequestContext): Promise<void> {
  const email = `films-${Date.now()}@example.com`;
  const password = "correct horse battery staple";
  await page.goto("/signup");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign up" }).click();
  // Wait for the signup to land before reading the mailbox, or the read races the send.
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Check your email");
  await page.goto(await verificationPath(request, email));
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Verify and log in" }).click();
  await expect(page).toHaveURL(/\/watchlist$/);
}
