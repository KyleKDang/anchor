import { expect, test } from "@playwright/test";

import { verificationPath } from "./mail";

const destinations = ["Watchlist", "Discovery", "Rated", "Search", "Profile"];

test("a visitor signs up, verifies through the emailed link, logs out, logs in, walks the destinations, and logs out", async ({
  page,
  request,
}) => {
  const email = `smoke-${Date.now()}@example.com`;
  const password = "correct horse battery staple";

  await page.goto("/signup");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign up" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Check your email");

  await page.goto(await verificationPath(request, email));
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Finish signing up");
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Verify and log in" }).click();
  // A brand-new account lands on the entry fork, which is the one screen before the
  // frame; answering it either way is the last step of arriving.
  await expect(page).toHaveURL(/\/welcome$/);
  await page.getByRole("button", { name: "Start fresh" }).click();
  await expect(page).toHaveURL(/\/warmup$/);

  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Profile" }).click();
  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Log in");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page).toHaveURL(/\/watchlist$/);

  for (const destination of destinations) {
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: destination }).click();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(destination);
  }

  await expect(page.getByText(`Logged in as ${email}`)).toBeVisible();
  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("status")).toHaveText("You are logged out.");
  await page.goto("/watchlist");
  await expect(page).toHaveURL(/\/login$/);
});
