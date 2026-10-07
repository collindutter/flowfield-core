import { existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";

const state = process.env.FLOWFIELD_SMOKE_STATE!;

test("directory adoption keeps cancellation harmless and opens project chat", async ({
  page,
  request,
}, testInfo) => {
  const directory = join(state, "native-directory-project");
  mkdirSync(directory, { recursive: true });
  let path: string | null = null;
  await page.route("**/api/projects/select-directory", (route) => {
    expect(route.request().method()).toBe("POST");
    return route.fulfill({ json: { path } });
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Coordinator" })).toHaveCount(
    0,
  );
  await page
    .getByRole("button", { name: "Choose directory", exact: true })
    .click();
  await expect(
    page.getByRole("textbox", { name: "Project directory" }),
  ).toHaveCount(0);
  expect(existsSync(join(directory, ".flowfield"))).toBe(false);
  path = directory;
  await page
    .getByRole("button", { name: "Choose directory", exact: true })
    .click();
  await expect(
    page.getByRole("textbox", { name: "Project directory" }),
  ).toHaveValue(directory);
  await expect(
    page.getByRole("textbox", { name: "Project directory" }),
  ).toHaveAttribute("readonly", "");
  expect(existsSync(join(directory, ".flowfield"))).toBe(false);
  const setup = page.getByRole("region", {
    name: "Project setup instructions",
  });
  await expect(
    setup.getByRole("textbox", { name: "Name", exact: true }),
  ).toHaveValue("native-directory-project");
  await expect(
    setup.getByRole("textbox", { name: "Project ID", exact: true }),
  ).toHaveValue("native-directory-project");
  await expect(
    setup.getByRole("textbox", { name: "Task prefix", exact: true }),
  ).toHaveValue("NAT");
  for (const label of ["Name", "Project ID", "Task prefix"]) {
    const field = setup.getByRole("textbox", { name: label, exact: true });
    const value = await field.inputValue();
    await expect(field).toHaveAttribute("required", "");
    await field.fill("");
    await setup
      .getByRole("button", { name: "Add project", exact: true })
      .click();
    await expect(field).toBeFocused();
    expect(existsSync(join(directory, ".flowfield"))).toBe(false);
    await field.fill(value);
  }
  await expect(
    setup.getByRole("heading", { name: "Start planning", exact: true }),
  ).toHaveCount(0);
  await expect(
    setup.getByText("Add a project from the CLI", { exact: true }),
  ).toHaveCount(0);
  await expect(
    setup.getByText("Use a standalone coding agent", { exact: true }),
  ).toHaveCount(0);
  await expect(setup.getByText(/Registration adds/)).toHaveCount(0);
  for (const label of ["Project ID", "Task prefix"]) {
    const field = setup.getByRole("textbox", { name: label, exact: true });
    const description = setup.locator(
      `#${await field.getAttribute("aria-describedby")}`,
    );
    const fieldBox = (await field.boundingBox())!;
    expect(
      Math.abs(
        (await description.boundingBox())!.y - fieldBox.y - fieldBox.height,
      ),
    ).toBeLessThan(1);
  }
  await setup.getByLabel("Install project guidance", { exact: true }).uncheck();
  const explanation = setup.getByText(
    "Helps standalone agents coordinate work and prepares workers. Existing instructions are preserved.",
    { exact: true },
  );
  const skipped = setup.getByText(
    "Install later in Project settings → Coordinator. The built-in Coordinator can already help you set up workers.",
    { exact: true },
  );
  const explanationBox = (await explanation.boundingBox())!;
  const skippedBox = (await skipped.boundingBox())!;
  expect(
    Math.abs(skippedBox.y - explanationBox.y - explanationBox.height),
  ).toBeLessThan(1);
  const previewBox = (await setup
    .getByText("Preview project guidance", { exact: true })
    .boundingBox())!;
  expect(previewBox.y).toBeGreaterThanOrEqual(skippedBox.y + skippedBox.height);
  await setup.evaluate((el) => el.scrollTo({ top: 0 }));
  await page.screenshot({
    path: testInfo.outputPath("project-form-guidance-skipped.png"),
  });
  await setup.getByLabel("Install project guidance", { exact: true }).check();
  await setup.getByText("Preview project guidance", { exact: true }).click();
  await expect(
    setup.locator("pre").filter({ hasText: "<!-- flowfield:begin -->" }),
  ).toBeVisible();
  await expect(
    setup.getByRole("heading", { name: "AGENTS.md section", exact: true }),
  ).toBeVisible();
  await expect(
    setup.getByRole("heading", {
      name: ".agents/skills/flowfield-coordinator/SKILL.md",
      exact: true,
    }),
  ).toHaveCount(1);
  const setupBox = (await setup.boundingBox())!;
  const paneBox = (await page.locator(".workspace-pane").last().boundingBox())!;
  expect(
    Math.abs(setupBox.x + setupBox.width - paneBox.x - paneBox.width),
  ).toBeLessThan(2);
  expect(await setup.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(
    true,
  );
  expect(
    await setup
      .locator(".welcome")
      .evaluate((el) => getComputedStyle(el).overflowY),
  ).toBe("visible");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollHeight <= innerHeight,
    ),
  ).toBe(true);
  await setup
    .getByRole("heading", { name: "AGENTS.md section", exact: true })
    .scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("guidance-preview.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  const skillTitle = setup.getByRole("heading", {
    name: ".agents/skills/flowfield-coordinator/SKILL.md",
    exact: true,
  });
  await skillTitle.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: testInfo.outputPath("guidance-preview-mobile.png"),
  });
  await page.setViewportSize({ width: 1280, height: 720 });
  expect(existsSync(join(directory, "AGENTS.md"))).toBe(false);
  await setup.getByText("Preview project guidance", { exact: true }).click();
  await setup.evaluate((el) => el.scrollTo({ top: 0 }));
  await page.screenshot({
    path: testInfo.outputPath("guidance-at-adoption.png"),
  });
  await setup.getByRole("button", { name: "Add project", exact: true }).click();
  await expect(page).toHaveURL("/projects/native-directory-project");
  await expect(
    page.getByRole("heading", { name: "Coordinator" }),
  ).toBeVisible();
  expect(existsSync(join(directory, ".flowfield/config.toml"))).toBe(true);
  expect(existsSync(join(directory, "AGENTS.md"))).toBe(true);
  expect(
    existsSync(
      join(directory, ".agents/skills/flowfield-coordinator/SKILL.md"),
    ),
  ).toBe(true);

  await page.goto("/projects/native-directory-project/edit/integration");
  await expect(page.getByLabel("Validation commands")).toHaveValue("");
  await expect(page.getByLabel("Use Local host")).toHaveCount(0);
  const saved = await (
    await request.get("/api/projects/native-directory-project/integration")
  ).json();
  expect(saved.runtime).toBe("local");
  expect(saved.checks).toEqual([]);
  expect(saved.target_branch).toBeNull();
  await page.reload();
  await expect(page.getByLabel("Use Local host")).toHaveCount(0);
  await page.screenshot({
    path: testInfo.outputPath("local-before-worker-settings.png"),
  });
  await page.getByRole("button", { name: "Close editor", exact: true }).click();
  await page.getByRole("button", { name: "Add project", exact: true }).click();
  await expect(page).toHaveURL("/new-project");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("button", { name: "Choose directory", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("project-setup-mobile.png"),
  });
});

test("registration conflict retains selection for a unique project ID", async ({
  page,
  request,
}, testInfo) => {
  const existing = join(state, "first", "picked-collision");
  const selected = join(state, "second", "picked-collision");
  mkdirSync(existing, { recursive: true });
  mkdirSync(selected, { recursive: true });
  expect(
    (
      await request.post("/api/projects/initialize", {
        data: { path: existing },
      })
    ).ok(),
  ).toBe(true);
  await page.route("**/api/projects/select-directory", (route) =>
    route.fulfill({ json: { path: selected } }),
  );
  await page.goto("/new-project");
  await page
    .getByRole("button", { name: "Choose directory", exact: true })
    .click();
  const setup = page.getByRole("region", {
    name: "Project setup instructions",
  });
  await setup.getByRole("button", { name: "Add project", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("another directory");
  expect(existsSync(join(selected, ".flowfield"))).toBe(false);
  await setup
    .getByRole("textbox", { name: "Project ID", exact: true })
    .fill("picked-unique");
  await setup.getByRole("button", { name: "Add project", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(
    "prefix PIC is already used",
  );
  await setup
    .getByRole("textbox", { name: "Task prefix", exact: true })
    .fill("PKU");
  await setup.getByLabel("Install project guidance", { exact: true }).uncheck();
  await expect(setup).toContainText("Install later in Project settings");
  await setup.getByRole("button", { name: "Add project", exact: true }).click();
  await expect(page).toHaveURL("/projects/picked-unique");
  expect(existsSync(join(selected, "AGENTS.md"))).toBe(false);
  await expect(setup).not.toBeVisible();
  await page.getByRole("button", { name: "Add project", exact: true }).click();
  await expect(page).toHaveURL("/new-project");
  await expect(
    page.getByRole("heading", { name: "Set up your project", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("project-setup-desktop.png"),
  });
});
