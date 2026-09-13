import { expect, test, type Page } from "@playwright/test";

import { letterboxdExport } from "./export";
import { verificationPath } from "./mail";
import { PASSWORD, signUpOwner } from "./owner";

/**
 * A fresh account, from signing up to a usable one, through the whole of onboarding.
 *
 * The one journey that does not use the shared sign-up helper, because the helper's job
 * is to get past the entry fork and this is the journey that walks through it. It covers
 * the wiring rather than the behaviour, per testing.md: that the fork leads somewhere,
 * that marking from search runs the picker and comes back, that the rating and backlog
 * phases work, and that the app is usable at the end of it.
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

  // There is no separate designation flow any more: rate a film, mark it, and the band's
  // pool exists. A film nobody has rated goes to the picker first.
  await mark(page, "Fight Club", "5.0");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("How was it?");
  await pickBand(page, "5.0");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Fight Club landed");
  // The band rode to the picker and back, so the owner returns to the prompt that sent
  // them rather than to wherever the run would otherwise have moved on to.
  await page.getByRole("link", { name: "Leave it where it is" }).click();
  await expect(page).toHaveURL(/\/warmup\?band=5\.0$/);

  // Back on the prompt, the film it just rated is offered as the band's own candidate,
  // and marking it is the one tap that makes the pool exist.
  await page.getByRole("button", { name: /Fight Club/ }).click();
  await expect(page.getByText(/Marked: Fight Club/)).toBeVisible();

  // Any number may be marked per band, so the run holds here rather than moving on: the
  // second anchor comes out of the same list the first did.
  await expect(page.getByRole("heading", { name: /definitive 5\.0/ })).toBeVisible();
  await mark(page, "Whiplash", "5.0");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("How was it?");
  await pickBand(page, "5.0");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Whiplash landed");
  await page.getByRole("link", { name: "Leave it where it is" }).click();
  await page.getByRole("button", { name: /Whiplash/ }).click();
  // Most recently marked first, which is the order the exemplar cap reads.
  await expect(page.getByText("Marked: Whiplash, Fight Club")).toBeVisible();

  // Leaving an answered band is "next"; leaving an unanswered one is a skip, and one
  // band can be put away alone.
  await page.getByRole("button", { name: "Next band" }).click();
  await expect(page.getByRole("heading", { name: /definitive 1\.0/ })).toBeVisible();
  await page.getByRole("button", { name: "Skip this band" }).click();
  await expect(page.getByRole("heading", { name: /definitive 3\.0/ })).toBeVisible();

  // Phase 2 on this fill is ordinary ratings, and the anchor's own rating is not one of
  // them: the phase asks for five *more* than phase one already produced.
  await expect(page.getByText(/0 of about 5 so far/)).toBeVisible();

  // Phase 3: the backlog, which is usable from the moment it holds anything.
  await page.getByLabel("Find something to watch").fill("Parasite");
  await page
    .getByRole("search")
    .filter({ has: page.getByLabel("Find something to watch") })
    .getByRole("button", { name: "Search" })
    .click();
  await page.getByRole("button", { name: "Add to watchlist" }).first().click();
  await expect(page.getByText("1 film on your watchlist")).toBeVisible();

  // Putting the warmup away leaves an account that works, with what the warmup built.
  await page.getByRole("button", { name: /I'm done for now|Take me in/ }).click();
  await expect(page).toHaveURL(/\/rated$/);
  await expect(page.getByRole("link", { name: "Fight Club" })).toBeVisible();
  // The stars are the band the owner picked, which is what a rating is.
  await expect(page.getByRole("heading", { name: /5\.0/ })).toBeVisible();

  await page
    .getByRole("navigation", { name: "Main" })
    .getByRole("link", { name: "Watchlist" })
    .click();
  await expect(page.getByRole("link", { name: "Parasite" })).toBeVisible();

  // Dismissed is put away, not destroyed: Profile is where it stays reachable, along
  // with the import the fork offered on the other branch.
  await page
    .getByRole("navigation", { name: "Main" })
    .getByRole("link", { name: "Profile" })
    .click();
  await expect(page.getByRole("link", { name: "Pick up the warmup" })).toBeVisible();
  // Exact: this is the area's own heading, and the sync list inside it has one of its
  // own that says "Letterboxd" too - a warmed-up account has ratings Letterboxd never saw.
  await expect(page.getByRole("heading", { name: "Letterboxd", exact: true })).toBeVisible();
});

/**
 * The other fill: an account that arrived holding ratings, and the step that differs.
 *
 * Its middle step is looking over the wall the export just built rather than rating more
 * films, and that step happens on another screen - so what only a running stack can show
 * is that the warmup sends the owner into edit mode, that the explanation is waiting
 * there when they arrive, and that the moves they make there come back as progress.
 */
test("an owner who imported looks over the wall and moves a few films", async ({
  page,
  request,
}) => {
  await signUpOwner(page, request, "warmup-import");

  await page
    .getByRole("navigation", { name: "Main" })
    .getByRole("link", { name: "Profile" })
    .click();
  await page.getByLabel("Your Letterboxd export (.zip)").setInputFiles({
    name: "letterboxd-owner-2026-08-02-11-00-utc.zip",
    mimeType: "application/zip",
    buffer: letterboxdExport(
      // Two in the band the run opens on, so the first prompt has a shortlist to mark
      // from and a second film left in it once the first is marked.
      [
        { name: "Fight Club", year: 1999, rating: 5 },
        { name: "Arrival", year: 2016, rating: 5 },
        { name: "Heat", year: 1995, rating: 4 },
      ],
      [],
    ),
  });
  await page.getByRole("button", { name: "Import your export" }).click();
  await expect(page.getByText("Every row found its film.")).toBeVisible({ timeout: 60_000 });

  // The fill is read off what the account holds, so the warmup is the import's from here.
  await page.getByRole("link", { name: "Pick up the warmup" }).click();
  await expect(page).toHaveURL(/\/warmup$/);
  await expect(page.getByRole("heading", { name: "2. Look over the wall" })).toBeVisible();
  await expect(page.getByText("Nothing moved yet.")).toBeVisible();

  // Step 1 offers the owner's own films, and takes as many marks per band as they give it.
  await page.getByRole("button", { name: /Fight Club/ }).click();
  await expect(page.getByText(/Marked: Fight Club/)).toBeVisible();
  await page.getByRole("button", { name: /Arrival/ }).click();
  await expect(page.getByText("Marked: Arrival, Fight Club")).toBeVisible();

  // Step 2 is a link, because the step happens on the wall. The explanation is waiting
  // there rather than here: it is about dragging, and here is not where dragging is.
  await expect(page.getByText(/This is your wall/)).toHaveCount(0);
  await page.getByRole("link", { name: "Open the wall" }).click();
  await expect(page).toHaveURL(/\/rated\?edit=1$/);
  await expect(page.getByText(/This is your wall/)).toBeVisible();

  // A poster is a keyboard control, and each step is a move saved at once. Three films
  // moved is what the step asks for, and the explanation goes with the first of them.
  await step(page, "Fight Club", 4.5);
  await expect(page.getByText(/This is your wall/)).toHaveCount(0);
  await step(page, "Arrival", 4.5);
  await step(page, "Heat", 3.5);

  // Back on the warmup, the moves are the step's progress and it has stopped asking.
  await page
    .getByRole("navigation", { name: "Main" })
    .getByRole("link", { name: "Profile" })
    .click();
  await page.getByRole("link", { name: "Pick up the warmup" }).click();
  await expect(page.getByText("3 of about 3 moved so far.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "2. Look over the wall" })).toContainText("done");
});

/**
 * Move a poster down a band with the keyboard, which is a drop by another gesture.
 *
 * The wall re-reads itself once the saves behind it drain, so each step waits for its
 * film to be showing in the band it asked for before the next one goes: a focus call
 * racing that re-read would land on a poster about to be replaced.
 */
async function step(page: Page, title: string, band: number): Promise<void> {
  const saved = page.waitForResponse((response) => response.url().includes("/move"));
  await page.getByRole("button", { name: `Move ${title}` }).focus();
  await page.keyboard.press("ArrowDown");
  expect((await saved).ok()).toBe(true);
  await expect(page.getByRole("region", { name: `${band.toFixed(1)} stars` })).toContainText(title);
}

/** Tap a band on the picker, which is the whole of rating a film. */
async function pickBand(page: Page, band: string): Promise<void> {
  await page
    .getByRole("list", { name: "Pick a rating" })
    .getByRole("button")
    .filter({ hasText: band })
    .first()
    .click();
}

/** Search for a film inside the current band prompt and take it into the picker. */
async function mark(page: Page, title: string, band: string): Promise<void> {
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
