import { expect, test, type Page } from "@playwright/test";

import { letterboxdExport } from "./export";
import { signUpOwner } from "./owner";

/**
 * Four imported films: the fewest a sitting can pass on one, work through two, and still
 * have one left to hand over.
 *
 * Deliberately no more. Every row is a matching job on the one shared worker, the suite's
 * journeys run in parallel, and the skeleton journey proves that worker by waiting five
 * seconds on a probe job of its own - so a fat import here is a timeout over there.
 */
const LIBRARY = [
  { name: "Fight Club", year: 1999, rating: 5 },
  { name: "Arrival", year: 2016, rating: 4 },
  { name: "Heat", year: 1995, rating: 3 },
  { name: "Parasite", year: 2019, rating: 4.5 },
];

/**
 * A settling sitting, from the strip that opens it to the count coming down.
 *
 * The journey the ticket asks for, with the pass folded into it: an imported account opens
 * settling from Rated, passes on the first film offered, settles two in a row, leaves
 * part-way through a fourth, and finds the strip's count down by exactly the two that
 * graduated. One journey rather than two because it needs only one import to prove all of
 * it, and imports are what the shared worker is busiest with.
 *
 * What is proved here is the wiring the API seam cannot reach - that the strip is on the
 * wall, that its button opens a stream, that a landing leads to the next film without
 * leaving it, and that the ways out are always there. Which film the engine picks, how
 * many questions each takes, and what a pass or a departure stores are all pinned at the
 * API seam, so nothing here names any of them.
 */
test("an owner passes on one film, settles two, and leaves the sitting part-way through", async ({
  page,
  request,
}) => {
  await signUpOwner(page, request, "sitting");
  await importLibrary(page);

  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Rated" }).click();
  const strip = page.getByRole("region", { name: /still settling/ });
  await expect(strip).toContainText("4 films are still settling");

  // The strip's button is the door, and it opens the stream rather than one film's page.
  await strip.getByRole("link", { name: "Settle them" }).click();
  await expect(page).toHaveURL(/\/settling$/);
  const named = page.locator(".sitting-film");
  await expect(named).toContainText("Settling");
  await expect(page.getByText("1 of about 4")).toBeVisible();
  await expect(page.getByRole("link", { name: "Leave settling" })).toBeVisible();
  await expect(page.getByText("Nothing settled yet this sitting.")).toBeVisible();

  // "Not this one" moves the sitting on and leaves the film as it found it. That the
  // declined film keeps nothing - not a judgment, and not the ask that opening it
  // recorded - is pinned at the API seam; what this proves is that the button moves on.
  const passed = (await named.textContent()) ?? "";
  await page.getByRole("button", { name: "Not this one" }).click();
  await expect(named).not.toHaveText(passed);
  await expect(page.getByText("2 of about 4")).toBeVisible();

  // The first settle: answered through to its landing, whose primary is the next film.
  await answerUntilLanded(page);
  await expect(page.getByText("1 film settled")).toBeVisible();
  await page.getByRole("button", { name: "Next film" }).click();
  await expect(page.getByText("3 of about 4")).toBeVisible();

  // The second: the same again, without ever leaving the stream.
  await answerUntilLanded(page);
  await expect(page.getByText("2 films settled")).toBeVisible();
  await page.getByRole("button", { name: "Next film" }).click();
  await expect(page.getByText("4 of about 4")).toBeVisible();

  // A fourth film is on screen and the owner walks away from it. Leaving is always there.
  await page.getByRole("link", { name: "Leave settling" }).click();
  await expect(page).toHaveURL(/\/rated$/);

  // Two films graduated, so the count is down by two - and no more. The passed film is
  // exactly where the sitting found it, because passing stored nothing about it.
  await expect(page.getByRole("region", { name: /still settling/ })).toContainText(
    "2 films are still settling",
  );
});

/** Import a small library, every film landing on a placeholder position. */
async function importLibrary(page: Page): Promise<void> {
  await page
    .getByRole("navigation", { name: "Main" })
    .getByRole("link", { name: "Profile" })
    .click();
  await page.getByLabel("Your Letterboxd export (.zip)").setInputFiles({
    name: "letterboxd-owner-2026-08-02-11-00-utc.zip",
    mimeType: "application/zip",
    buffer: letterboxdExport(LIBRARY, []),
  });
  await page.getByRole("button", { name: "Import your export" }).click();
  await expect(page.getByText("Every row found its film.")).toBeVisible({ timeout: 60_000 });
}

/**
 * Answer whatever the flow asks, taking the first option offered, until the film lands.
 *
 * How many questions a settle takes depends on what the film has already collected, which
 * is the head start's business and is pinned at the API seam - so this answers until the
 * landing appears rather than a fixed number of times. Each click waits for its own
 * response, so the next turn reads the step that answer produced rather than the one it
 * replaced.
 */
async function answerUntilLanded(page: Page): Promise<void> {
  const next = page.getByRole("button", { name: "Next film" });
  const answers = page.getByRole("button", { name: /is better$|is a \d/ });
  for (let step = 0; step < 10 && !(await next.isVisible()); step += 1) {
    await Promise.all([
      page.waitForResponse(
        (response) =>
          response.url().includes("/api/placements/") && response.request().method() === "POST",
      ),
      answers.first().click(),
    ]);
    // The response lands a beat before the screen redraws, and every control is disabled
    // while the answer is in flight - so wait for the next step to be answerable rather
    // than clicking the button this answer is about to replace.
    await expect(page.getByRole("button", { disabled: true })).toHaveCount(0);
  }
  await expect(next).toBeVisible();
}
