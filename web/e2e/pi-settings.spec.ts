import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect } from "@playwright/test";
import { test } from "./support";

test("Pi coordinator settings select a separate catalog and persist the harness", async ({
  page,
  request,
}) => {
  const path = join(process.env.FLOWFIELD_SMOKE_STATE!, "pi-settings");
  mkdirSync(path, { recursive: true });
  const response = await request.post("/api/projects/initialize", {
    data: { path, task_prefix: "PIS" },
  });
  expect(response.ok()).toBe(true);
  let settings: Record<string, unknown> = {
    revision: 1,
    selection: null,
    effective: null,
  };
  const discoveries: string[] = [];
  await page.route("**/api/worker-models*", async (route) => {
    const harness = new URL(route.request().url()).searchParams.get("harness");
    discoveries.push(harness!);
    await route.fulfill({
      json:
        harness === "pi"
          ? [
              {
                harness: "pi",
                id: "provider/test-model",
                name: "Pi test model",
                efforts: ["off", "low"],
                modes: [
                  {
                    id: "full-access",
                    name: "Full host access",
                    description: "Unsandboxed access to the service host.",
                  },
                ],
                fast: false,
              },
            ]
          : [],
    });
  });
  await page.route(
    "**/api/projects/pi-settings/coordinator-settings",
    async (route) => {
      if (route.request().method() === "PUT") {
        const body = route.request().postDataJSON();
        expect(body.selection).toEqual({
          harness: "pi",
          model: "provider/test-model",
          effort: "low",
          mode: "full-access",
          fast: false,
        });
        settings = {
          revision: 2,
          selection: body.selection,
          effective: {
            choice: body.selection,
            source: "project",
            default_revision: 2,
            override_revision: null,
          },
        };
      }
      await route.fulfill({ json: settings });
    },
  );
  await page.route("**/coordinator/commands/discover*", (route) =>
    route.fulfill({ json: [] }),
  );
  await page.goto("/projects/pi-settings");
  const picker = page.getByRole("dialog", {
    name: "Coordinator model settings",
  });
  await expect(picker).toBeVisible();
  await picker.getByLabel("Harness", { exact: true }).selectOption("pi");
  await expect(picker.getByLabel("Model", { exact: true })).toBeEnabled();
  await picker
    .getByLabel("Model", { exact: true })
    .selectOption("provider/test-model");
  await picker.getByLabel("Reasoning effort").selectOption("low");
  await picker.getByLabel("Access mode").selectOption("full-access");
  await expect(
    picker.getByText("Unsandboxed access to the service host."),
  ).toBeVisible();
  await picker.getByRole("button", { name: "Save", exact: true }).click();
  await expect(picker).not.toBeVisible();
  await expect(
    page.getByRole("button", { name: "provider/test-model · low" }),
  ).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "provider/test-model · low" }).click();
  await expect(picker.getByLabel("Harness", { exact: true })).toHaveValue("pi");
  await expect(picker.getByLabel("Model", { exact: true })).toHaveValue(
    "provider/test-model",
  );
  expect(discoveries).toContain("pi");
});

test("empty settings prefer an installed harness without probing Codex", async ({
  page,
  request,
}) => {
  const path = join(process.env.FLOWFIELD_SMOKE_STATE!, "pi-only");
  mkdirSync(path, { recursive: true });
  expect(
    (
      await request.post("/api/projects/initialize", {
        data: { path, task_prefix: "PIO" },
      })
    ).ok(),
  ).toBe(true);
  await page.route("**/api/agent-harnesses", (route) =>
    route.fulfill({
      json: [
        {
          id: "codex",
          name: "Codex",
          description: "ACP",
          available: false,
          message: "Install the managed Codex runtime",
        },
        {
          id: "pi",
          name: "Pi",
          description: "RPC",
          available: true,
          message: "Installed",
        },
      ],
    }),
  );
  const probes: string[] = [];
  await page.route("**/api/worker-models*", (route) => {
    probes.push(new URL(route.request().url()).searchParams.get("harness")!);
    return route.fulfill({ json: [] });
  });
  await page.goto("/projects/pi-only");
  const picker = page.getByRole("dialog", {
    name: "Coordinator model settings",
  });
  await expect(picker.getByLabel("Harness", { exact: true })).toHaveValue("pi");
  await expect.poll(() => probes.length).toBeGreaterThan(0);
  await picker.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.goto("/projects/pi-only/edit/workers");
  const workers = page.getByRole("region", { name: "Worker settings" });
  await expect(workers.getByLabel("Harness", { exact: true })).toHaveValue(
    "pi",
  );
  await expect(
    page.getByText("Install the managed Codex runtime", { exact: true }),
  ).toHaveCount(0);
  expect(probes.every((harness) => harness === "pi")).toBe(true);
});

test("harness picker renders registry entries without client changes", async ({
  page,
  request,
}) => {
  const path = join(process.env.FLOWFIELD_SMOKE_STATE!, "registry-settings");
  mkdirSync(path, { recursive: true });
  expect(
    (
      await request.post("/api/projects/initialize", {
        data: { path, task_prefix: "REG" },
      })
    ).ok(),
  ).toBe(true);
  await page.route("**/api/agent-harnesses", (route) =>
    route.fulfill({
      json: [
        {
          id: "another-agent",
          name: "Another agent",
          description: "Registered adapter",
          available: true,
          message: "",
        },
      ],
    }),
  );
  await page.goto("/projects/registry-settings");
  const picker = page.getByRole("dialog", {
    name: "Coordinator model settings",
  });
  await picker
    .getByLabel("Harness", { exact: true })
    .selectOption("another-agent");
  await expect(picker.getByLabel("Harness", { exact: true })).toHaveValue(
    "another-agent",
  );
});
