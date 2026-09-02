import { expect, type APIRequestContext, type Page } from "@playwright/test";

import { verificationPath } from "./mail";

export const PASSWORD = "correct horse battery staple";

/**
 * Sign up, verify through the emailed link, and land logged in.
 *
 * Every journey but the auth one starts here, and none of them is testing signup, so
 * they share this rather than each repeating the four screens it takes. `prefix` keeps
 * the addresses apart when the suite runs its journeys in parallel.
 */
export async function signUpOwner(
  page: Page,
  request: APIRequestContext,
  prefix: string,
): Promise<void> {
  const email = `${prefix}-${Date.now()}@example.com`;
  await page.goto("/signup");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign up" }).click();
  // Wait for the signup to land before reading the mailbox, or the read races the send.
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Check your email");
  await page.goto(await verificationPath(request, email));
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Verify and log in" }).click();
  await expect(page).toHaveURL(/\/watchlist$/);
}
