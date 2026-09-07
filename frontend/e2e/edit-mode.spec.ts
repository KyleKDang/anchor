import { expect, test, type Locator, type Page } from "@playwright/test";

import { signUpOwner } from "./owner";

/**
 * Edit mode on the wall: the toggle, a drag across bands, and the wall after a reload.
 *
 * Wiring, not behaviour - renumbering, the cross-band retire, and the filter rule are
 * pinned at the API seam - but nothing below the browser can show that a real drag ends
 * in a saved move, or that the toggle on a poster marks and retires.
 */
test("an owner drags a film across bands and finds it there after a reload", async ({
  page,
  request,
}) => {
  await signUpOwner(page, request, "edit");
  await rate(page, "Arrival", 4);
  await rate(page, "Fight Club", 2.5);
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Rated" }).click();

  // The one ambient line about anchors belongs to edit mode, and vanishes at the first mark.
  await expect(page.getByText(/Marking a film an anchor/)).toHaveCount(0);
  await page.getByRole("button", { name: "Edit the wall" }).click();
  await expect(page).toHaveURL(/\/rated\?edit=1$/);
  await expect(page.getByText(/Marking a film an anchor/)).toBeVisible();
  await page.getByRole("button", { name: "Mark Arrival as an anchor" }).click();
  await expect(page.getByRole("button", { name: "Retire Arrival as an anchor" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(page.getByText(/Marking a film an anchor/)).toHaveCount(0);
  await expect(page.getByRole("region", { name: "4.0 stars" })).toContainText("1 anchor");

  // Every band is a row in edit mode, the empty ones included, so the drag has a target.
  // Tall enough that both rows are in view: a pointer parked past the edge would auto-scroll
  // the wall under it and drop wherever the scroll stopped, which is not this journey.
  await page.setViewportSize({ width: 1280, height: 1600 });
  const saved = page.waitForResponse((response) => response.url().includes("/move"));
  await drag(
    page,
    page.getByRole("button", { name: "Move Arrival" }),
    page.getByRole("button", { name: "Move Fight Club" }),
  );
  const worse = page.getByRole("region", { name: "2.5 stars" });
  await expect(worse.getByRole("listitem").filter({ hasText: "Arrival" })).toBeVisible();
  expect((await saved).ok()).toBe(true);

  // The drop saved at once: a fresh load reads the move back, and the badge went with it.
  await page.reload();
  await expect(page.getByRole("region", { name: "2.5 stars" })).toContainText("Arrival");
  await expect(page.getByRole("region", { name: "4.0 stars" }).getByRole("listitem")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Mark Arrival as an anchor" })).toHaveAttribute(
    "aria-pressed",
    "false",
  );

  // Leaving edit mode is the same toggle, and the read-only wall agrees.
  await page.getByRole("button", { name: "Done editing" }).click();
  await expect(page).toHaveURL(/\/rated$/);
  const landed = page.getByRole("region", { name: "2.5 stars" }).getByRole("listitem");
  await expect(landed).toHaveCount(2);
  await expect(landed.filter({ hasText: "Arrival" }).getByText("Anchor", { exact: true })).toHaveCount(0);
});

/** A real pointer drag: a few pixels to start it, then a glide onto the target's cell. */
async function drag(page: Page, from: Locator, to: Locator): Promise<void> {
  const start = await from.boundingBox();
  const end = await to.boundingBox();
  expect(start).not.toBeNull();
  expect(end).not.toBeNull();
  await page.mouse.move(start!.x + start!.width / 2, start!.y + start!.height / 2);
  await page.mouse.down();
  await page.mouse.move(start!.x + start!.width / 2 + 12, start!.y + start!.height / 2 + 12, {
    steps: 4,
  });
  await page.mouse.move(end!.x + end!.width / 2, end!.y + end!.height / 2, { steps: 25 });
  await page.waitForTimeout(200);
  await page.mouse.up();
}

/** Search for a film, log the watch, and tap a band on the picker. */
async function rate(page: Page, title: string, band: number): Promise<void> {
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Search" }).click();
  await page.getByLabel("Find a film").fill(title);
  await page.getByRole("button", { name: "Search" }).click();
  const row = page.getByRole("listitem").filter({ hasText: title });
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "Mark watched" }).click();
  await row.getByRole("button", { name: "Rate now", exact: true }).click();
  await page
    .getByRole("list", { name: "Pick a rating" })
    .getByRole("button")
    .filter({ hasText: `${band.toFixed(1)}` })
    .first()
    .click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(`${title} landed`);
  await page.getByRole("link", { name: "Leave it where it is" }).click();
}
