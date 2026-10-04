import { expect, test } from "@playwright/test";

const fixture = "/e2e/fixtures/workspace/index.html";
test.beforeEach(async ({ page }) => {
  await page.goto(fixture);
});

test("standard sidebar toggles, names remain accessible, project drafts stay separate", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1400, height: 900 });
  const message = page.getByRole("textbox", { name: "Message coordinator" });
  await message.fill("Remember this discussion");
  await page
    .getByRole("button", { name: "Collapse projects", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Expand projects", exact: true }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Field Notes", exact: true }).click();
  await expect(message).toHaveValue("");
  await page.getByRole("link", { name: "Harbor", exact: true }).click();
  await expect(message).toHaveValue("Remember this discussion");
  await page.keyboard.press("Control+b");
  await expect(
    page.getByRole("button", { name: "Collapse projects", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Collapse projects", exact: true })
    .click();
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Expand projects", exact: true }),
  ).toBeVisible();
});

test("board owns overflow, divider resizes with keyboard, references preserve drafts", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1400, height: 900 });
  const scroll = page.locator(".workspace-board-scroll");
  await expect(scroll).toBeVisible();
  expect(
    await scroll.evaluate((node) => node.scrollWidth > node.clientWidth),
  ).toBe(true);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  const pane = page.locator(".workspace-pane").first();
  const before = await pane.boundingBox();
  const divider = page.getByRole("separator", {
    name: "Resize coordinator and work",
  });
  await divider.focus();
  await page.keyboard.press("ArrowRight");
  await expect
    .poll(async () => (await pane.boundingBox())?.width)
    .toBeGreaterThan(before!.width);
  await page
    .getByRole("textbox", { name: "Message coordinator" })
    .fill("Keep my draft");
  await page.getByRole("link", { name: "HBR-10", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("textbox", { name: "Message coordinator" }),
  ).toHaveValue("Keep my draft");
});

test("mobile drawer, surface tabs and viewport changes keep the composer usable", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const message = page.getByRole("textbox", { name: "Message coordinator" });
  await message.fill("Mobile draft");
  const conversation = page.locator(".fixture-conversation");
  await conversation.evaluate((node) => {
    node.scrollTop = 120;
  });
  const scrollTop = await conversation.evaluate((node) => node.scrollTop);
  await page.getByRole("tab", { name: "Work", exact: true }).click();
  await expect(message).not.toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Harbor", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.getByRole("tab", { name: "Coordinator", exact: true }).click();
  await expect(message).toHaveValue("Mobile draft");
  expect(await conversation.evaluate((node) => node.scrollTop)).toBe(scrollTop);
  await page
    .getByRole("button", { name: "Open projects", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("link", { name: "Atlas", exact: true }).click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await expect(message).toHaveValue("");
  await page.setViewportSize({ width: 1400, height: 900 });
  await expect(page.getByRole("separator")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Atlas", exact: true }),
  ).toBeVisible();
});
