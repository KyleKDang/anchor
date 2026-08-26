import { expect, test } from "@playwright/test";

/** The composed stack's fake Resend, where the verification link lands. */
const MAIL_URL = process.env.ANCHOR_MAIL_URL ?? "http://localhost:8025";

const destinations = ["Watchlist", "Discovery", "Rated", "Search", "Profile"];

test("a visitor signs up, verifies through the emailed link, logs in, walks the destinations, and logs out", async ({
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
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Email verified");
  await page.getByRole("link", { name: "Log in" }).click();

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

async function verificationPath(
  request: Parameters<Parameters<typeof test>[2]>[0]["request"],
  email: string,
): Promise<string> {
  const response = await request.get(`${MAIL_URL}/emails`);
  expect(response.ok()).toBe(true);
  const emails = (await response.json()) as { to: string[]; text: string }[];
  const message = emails.filter((candidate) => candidate.to.includes(email)).at(-1);
  expect(message, `no mail to ${email}`).toBeDefined();
  const match = /(\/verify\?token=[A-Za-z0-9_-]+)/.exec(message!.text);
  expect(match, message!.text).not.toBeNull();
  return match![1]!;
}
