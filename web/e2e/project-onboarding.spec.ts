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
  await expect(
    page.getByRole("heading", { name: "Coordinator Chat" }),
  ).toHaveCount(0);
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
  await setup.getByRole("button", { name: "Add project", exact: true }).click();
  await expect(page).toHaveURL("/projects/native-directory-project");
  await expect(
    page.getByRole("heading", { name: "Coordinator Chat" }),
  ).toBeVisible();
  expect(existsSync(join(directory, ".flowfield/config.toml"))).toBe(true);

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
  await setup.getByText("Project details (optional)", { exact: true }).click();
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
  await setup.getByRole("button", { name: "Add project", exact: true }).click();
  await expect(page).toHaveURL("/projects/picked-unique");
  await page.getByRole("button", { name: "Add project", exact: true }).click();
  await expect(page).toHaveURL("/new-project");
  await expect(
    page.getByRole("heading", { name: "Set up your project", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("project-setup-desktop.png"),
  });
});
