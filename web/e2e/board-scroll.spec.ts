import { join } from "node:path";
import { expect } from "@playwright/test";
import { test, existingDirectory, state, fixtureStages } from "./support";

test("board columns scroll independently with fixed headings and reachable cards", async ({
  page,
  request,
}, testInfo) => {
  const project = "column-scroll";
  expect(
    (
      await request.post("/api/projects/initialize", {
        data: {
          path: existingDirectory(join(state, project)),
          task_prefix: "BSC",
        },
      })
    ).ok(),
  ).toBe(true);
  for (const status of ["backlog", "up_next"]) {
    for (let i = 0; i < 14; i++) {
      expect(
        (
          await request.post(`/api/projects/${project}/tasks`, {
            data: {
              stages: fixtureStages(),
              title: `${status} task ${i}`,
              status,
            },
          })
        ).ok(),
      ).toBe(true);
    }
  }
  // Report occupancy without starting a model or changing the paused queue.
  await page.route(`**/api/projects/${project}/workers/occupancy`, (route) =>
    route.fulfill({ json: { active: 1, uncertain: 0 } }),
  );
  await page.route(`**/api/projects/${project}/workers`, async (route) => {
    const response = await route.fetch();
    await route.fulfill({
      json: { ...(await response.json()), max_parallel: 2 },
    });
  });
  await page.setViewportSize({ width: 1800, height: 800 });
  await page.goto(`/projects/${project}`);
  const board = page.locator(".board");
  const backlog = page.getByRole("region", {
    name: "Backlog tasks",
    exact: true,
  });
  const upNext = page.getByRole("region", {
    name: "Up next tasks",
    exact: true,
  });
  await expect(page.locator(".queue-controls")).toContainText(
    "Queue paused · 1/2 active",
  );
  await expect(backlog.getByRole("link")).toHaveCount(14);
  const headers = page.locator(".board .column > h2");
  const positions = () =>
    headers.evaluateAll((nodes) =>
      nodes.map((node) => node.getBoundingClientRect().top),
    );
  const initial = await positions();
  await backlog.focus();
  await page.keyboard.press("End");
  await expect
    .poll(() => backlog.evaluate((node) => node.scrollTop))
    .toBeGreaterThan(0);
  await expect(
    backlog.getByRole("link", { name: /backlog task 13$/ }),
  ).toBeInViewport();
  expect(await upNext.evaluate((node) => node.scrollTop)).toBe(0);
  expect(await positions()).toEqual(initial);
  await upNext.hover();
  await page.mouse.wheel(0, 700);
  await expect
    .poll(() => upNext.evaluate((node) => node.scrollTop))
    .toBeGreaterThan(0);
  expect(await positions()).toEqual(initial);
  for (const selector of [".workspace-work-content", ".board", "html"]) {
    expect(
      await page
        .locator(selector)
        .evaluate((node) => node.scrollHeight <= node.clientHeight),
    ).toBe(true);
  }
  await page.screenshot({
    path: testInfo.outputPath("board-columns-desktop.png"),
  });
  await page.setViewportSize({ width: 390, height: 640 });
  await expect
    .poll(() => board.evaluate((node) => node.scrollWidth > node.clientWidth))
    .toBe(true);
  await board.evaluate((node) => {
    node.scrollLeft = node.scrollWidth;
  });
  await expect(
    page.getByText("Finished outcomes", { exact: true }),
  ).toBeInViewport();
  expect(
    await page
      .locator(".workspace-work-content")
      .evaluate((node) => node.scrollHeight <= node.clientHeight),
  ).toBe(true);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("board-columns-mobile.png"),
  });
});
