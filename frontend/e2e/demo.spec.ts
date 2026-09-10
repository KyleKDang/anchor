import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

/**
 * The demo, walked the way a visitor walks it: one click from the front door, straight
 * onto the feed, every surface populated, a write intercepted, and no edit mode on the
 * wall (#108).
 *
 * Wiring, not behaviour. What the fixture builds is pinned at the API seam; this proves
 * the door opens, the surfaces show what the build put there, and the read-only
 * enforcement reaches the browser. The fixture is read off disk so the assertions name
 * the films it names, and a fixture edit that moves the top of the wall moves this too.
 */

interface Fixture {
  wall: { band: number; films: { title: string; anchor: boolean }[] }[];
  backlog: { title: string; pinned?: boolean }[];
}

const here = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(
  readFileSync(join(here, "..", "..", "backend", "src", "anchor", "demofixture.json"), "utf8"),
) as Fixture;

test("a visitor explores the demo from the front door and can look at everything but change nothing", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "Rank every film you've ever seen.",
  );

  // One click, no credentials, and the feed: the hero surface (demo-account.md).
  await page.getByRole("main").getByRole("link", { name: "Explore the demo" }).first().click();
  await expect(page).toHaveURL(/\/discovery$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Discovery");
  const shelf = page.getByRole("main").getByRole("list").first();
  await expect(shelf.getByRole("listitem").first()).toBeVisible();
  // No tour and no welcome overlay: the first thing on screen is the product.
  await expect(page.getByRole("dialog")).toHaveCount(0);

  // A write on the feed is intercepted by the pitch, and the shelf is untouched.
  const before = await shelf.getByRole("listitem").count();
  await shelf.getByRole("button", { name: "Add to backlog" }).first().click();
  const pitch = page.getByRole("dialog", { name: "This is a read-only demo" });
  await expect(pitch).toBeVisible();
  await expect(pitch.getByRole("link", { name: "Build your own" })).toBeVisible();
  await pitch.getByRole("button", { name: "Keep looking" }).click();
  await expect(pitch).toHaveCount(0);
  await expect(shelf.getByRole("listitem")).toHaveCount(before);

  // The wall: the fixture's bands in the fixture's order, and no way to edit it.
  const rail = page.getByRole("navigation", { name: "Main" });
  await rail.getByRole("link", { name: "Rated" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Rated");
  const top = fixture.wall[0]!;
  const topRow = page.getByRole("region", { name: `${top.band.toFixed(1)} stars` });
  await expect(topRow.getByRole("listitem").first()).toContainText(top.films[0]!.title);
  await expect(topRow).toContainText(`${top.films.filter((film) => film.anchor).length} anchor`);
  await expect(page.getByRole("region", { name: "0.5 stars" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Edit the wall" })).toHaveCount(0);

  // The watchlist: the up-next zone with its pin, and the ranked pool below it.
  await rail.getByRole("link", { name: "Watchlist" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Watchlist");
  const upNext = page.getByRole("region", { name: "Up next" });
  const pinned = fixture.backlog.find((film) => film.pinned);
  await expect(upNext).toContainText(pinned!.title);
  await expect(
    page.getByRole("region", { name: "In the running" }).getByRole("listitem").first(),
  ).toBeVisible();

  // The profile: the prose the pipeline wrote, and the demo said plainly, with no address
  // and no delete form.
  await rail.getByRole("link", { name: "Profile" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Profile");
  await expect(page.getByRole("region", { name: "What Anchor thinks you like" })).toBeVisible();
  const account = page.getByRole("region", { name: "Account" });
  await expect(account).toContainText("Demo account");
  await expect(account).not.toContainText("@");
  await expect(account.getByRole("button", { name: "Delete account" })).toHaveCount(0);

  // Leaving lands back on the front door, with no "logged out" line to explain.
  await account.getByRole("button", { name: "Leave the demo" }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "Rank every film you've ever seen.",
  );
});
