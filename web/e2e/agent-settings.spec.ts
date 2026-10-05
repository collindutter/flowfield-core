import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { test, expect } from "@playwright/test";

test("task settings preserve drafts, reject stale saves and reset; tool answers survive reload", async ({
  page,
  request,
}) => {
  const path = join(process.env.FLOWFIELD_SMOKE_STATE!, "agent-settings");
  mkdirSync(path, { recursive: true });
  expect(
    (
      await request.post("/api/projects/initialize", {
        data: { path, task_prefix: "AGS" },
      })
    ).ok(),
  ).toBe(true);
  const task = await (
    await request.post("/api/projects/agent-settings/tasks", {
      data: { title: "Check project behavior" },
    })
  ).json();
  const first = {
    harness: "codex",
    model: "first",
    effort: "low",
    mode: "workspace-write",
  };
  const modes = [
    {
      id: "workspace-write",
      name: "Workspace access",
      description: "Ask before accessing the network.",
    },
    {
      id: "read-only",
      name: "Read-only",
      description: "Ask before editing files.",
    },
  ];
  let settings = {
    revision: 1,
    selection: null as typeof first | null,
    effective: {
      choice: first,
      source: "project",
      default_revision: 1,
      override_revision: 1,
    },
  };
  await page.route(
    "**/api/projects/agent-settings/integration",
    async (route) => {
      const response = await route.fetch();
      await route.fulfill({
        json: { ...(await response.json()), runtime: "local" },
      });
    },
  );
  await page.route("**/api/worker-models", (route) =>
    route.fulfill({
      json: [
        { id: "first", name: "First", efforts: ["low", "high"], modes },
        { id: "second", name: "Second", efforts: ["medium"], modes },
      ],
    }),
  );
  await page.route("**/agent-settings", async (route) => {
    if (route.request().method() === "PUT") {
      const payload = route.request().postDataJSON();
      expect(payload.expected_revision).toBe(settings.revision);
      settings = {
        revision: settings.revision + 1,
        selection: payload.selection,
        effective: {
          choice: payload.selection ?? first,
          source: payload.selection ? "override" : "project",
          default_revision: 1,
          override_revision: settings.revision + 1,
        },
      };
    }
    await route.fulfill({ json: settings });
  });
  let permission = {
    id: "request-1",
    project_id: "agent-settings",
    role: "worker",
    task_id: task.id,
    run_id: "run",
    conversation_id: null,
    binding: "binding",
    session_id: "session",
    turn_id: "turn",
    tool_id: "check",
    title: "Run the project checks",
    details: "command: pnpm test\ncwd: /project/worktree",
    options: [
      { id: "allow", label: "Allow once", kind: "allow_once" },
      { id: "deny", label: "Reject once", kind: "reject_once" },
    ],
    revision: 1,
    status: "pending",
    answer: null as string | null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 300000).toISOString(),
    released_at: null as string | null,
  };
  await page.route("**/api/projects/agent-settings/permissions?*", (route) =>
    route.fulfill({
      json: {
        pending: permission.status === "pending" ? [permission] : [],
        items: permission.status === "pending" ? [] : [permission],
        next_before: null,
      },
    }),
  );
  await page.route("**/permissions/request-1/answer", async (route) => {
    const body = route.request().postDataJSON();
    expect(body).toEqual({ expected_revision: 1, option_id: "deny" });
    permission = {
      ...permission,
      revision: 2,
      status: "answered",
      answer: body.option_id,
      released_at: new Date().toISOString(),
    };
    await route.fulfill({ json: permission });
  });
  await page.goto(`/projects/agent-settings/tasks/${task.key}`);
  const detail = page.getByRole("region", { name: "Task details" });
  await detail.getByText("Worker settings", { exact: true }).click();
  await expect(detail.getByLabel("Model", { exact: true })).toHaveValue(
    "first",
  );
  await detail.getByLabel("Model", { exact: true }).selectOption("second");
  await expect(detail.getByLabel("Reasoning effort")).toHaveValue("");
  await detail.getByLabel("Reasoning effort").selectOption("medium");
  await detail.getByLabel("Native access mode").selectOption("read-only");
  await detail.getByLabel("Native access mode").scrollIntoViewIfNeeded();
  await page.screenshot({ path: "/tmp/flowfield-slice3-native-modes.png" });
  await detail.getByRole("button", { name: "Save agent settings" }).click();
  await expect(detail.getByText("Task override")).toBeVisible();
  expect(settings.selection?.mode).toBe("read-only");
  await detail.getByLabel("Model", { exact: true }).selectOption("first");
  await detail.getByLabel("Reasoning effort").selectOption("high");
  settings = { ...settings, revision: 3 };
  await request.post("/api/projects/agent-settings/tasks", {
    data: { title: "Cause settings refresh" },
  });
  await expect(
    detail.getByText(
      "Settings changed elsewhere. Load the latest settings before saving.",
    ),
  ).toBeVisible();
  await expect(detail.getByLabel("Reasoning effort")).toHaveValue("high");
  await expect(
    detail.getByRole("button", { name: "Save agent settings" }),
  ).toBeDisabled();
  page.once("dialog", (dialog) => dialog.accept());
  await detail.getByRole("button", { name: "Load latest settings" }).click();
  await expect(detail.getByLabel("Model", { exact: true })).toHaveValue(
    "second",
  );
  await detail.getByRole("button", { name: "Use project defaults" }).click();
  await expect(
    detail.getByText("Using project defaults", { exact: true }),
  ).toBeVisible();
  await detail
    .getByText("Run the project checks", { exact: true })
    .scrollIntoViewIfNeeded();
  await expect(
    detail.getByText("command: pnpm test", { exact: false }),
  ).toBeVisible();
  await page.screenshot({ path: "/tmp/flowfield-slice3-settings.png" });
  await detail.getByRole("button", { name: "Reject once" }).click();
  await expect(detail.getByRole("button", { name: "Allow once" })).toHaveCount(
    0,
  );
  await page.reload();
  await detail.getByText("Tool permission history", { exact: true }).click();
  await expect(detail.getByText(/Reject once · Answer recorded/)).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: "/tmp/flowfield-slice2-mobile.png" });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.route(
    "**/api/projects/agent-settings/coordinator-settings",
    (route) =>
      route.fulfill({
        json: { revision: 1, selection: null, effective: null },
      }),
  );
  await page.goto("/projects/agent-settings/edit/coordinator");
  const coordinator = page.getByRole("tabpanel", {
    name: "Coordinator",
    exact: true,
  });
  await expect(coordinator.getByLabel("Model", { exact: true })).toHaveCount(0);
  permission = {
    ...permission,
    id: "request-2",
    revision: 1,
    status: "pending",
    answer: null,
    released_at: null,
  };
  await page.route(
    "**/api/projects/agent-settings/view/attention?*",
    (route) => {
      const column = new URL(route.request().url()).searchParams.get("column");
      const shown =
        column === (permission.status === "pending" ? "action" : "history");
      return route.fulfill({
        json: {
          items: shown
            ? [
                {
                  id: permission.id,
                  kind: "permission",
                  task_key: task.key,
                  title: permission.title,
                  status: permission.status,
                  updated_at: permission.updated_at,
                },
              ]
            : [],
          total: shown ? 1 : 0,
          next_offset: null,
        },
      });
    },
  );
  await page.route("**/permissions/request-2?*", (route) =>
    route.fulfill({ json: permission }),
  );
  await page.route("**/permissions/request-2/answer", (route) => {
    expect(route.request().postDataJSON()).toEqual({
      expected_revision: 1,
      option_id: "allow",
    });
    permission = {
      ...permission,
      revision: 2,
      status: "answered",
      answer: "allow",
      released_at: new Date().toISOString(),
    };
    return route.fulfill({ json: permission });
  });
  await page.goto("/projects/agent-settings/inbox");
  const action = page.getByRole("region", {
    name: "Needs your action",
    exact: true,
  });
  await expect(
    action.getByRole("link", { name: task.key, exact: true }),
  ).toBeVisible();
  await expect(
    action.getByRole("button", { name: "Allow once" }),
  ).toBeVisible();
  await page.screenshot({ path: "/tmp/flowfield-slice2-attention.png" });
  await action.getByRole("button", { name: "Allow once" }).click();
  await expect(action.getByText(/Allow once · Answer recorded/)).toBeVisible();
});

test("Integration uses Local automatically and preserves drafts across project tabs", async ({
  page,
  request,
}) => {
  const path = join(process.env.FLOWFIELD_SMOKE_STATE!, "local-adoption");
  mkdirSync(path, { recursive: true });
  expect(
    (
      await request.post("/api/projects/initialize", {
        data: { path, task_prefix: "LOC" },
      })
    ).ok(),
  ).toBe(true);
  let settings = {
    project_id: "local-adoption",
    revision: 1,
    runtime: "local",
    target_branch: "main",
    checks: ["pnpm test"],
    setup_commands: ["pnpm install --frozen-lockfile"],
    setup_timeout_seconds: 120,
    check_timeout_seconds: 60,
    environment: {
      tools: { pnpm: "/old/pnpm" },
      read_paths: [],
      variables: { OLD: "retained" },
    },
  };
  await page.route(
    "**/api/projects/local-adoption/integration",
    async (route) => {
      if (route.request().method() === "PUT") {
        expect(route.request().postDataJSON()).not.toHaveProperty("runtime");
        expect(route.request().postDataJSON()).not.toHaveProperty(
          "environment",
        );
        settings = { ...settings, revision: 2, runtime: "local" };
      }
      await route.fulfill({ json: settings });
    },
  );
  await page.goto("/projects/local-adoption/edit/integration");
  const selection = page.getByLabel("Use Local host");
  await expect(selection).toHaveCount(0);
  await expect(page.getByLabel("Executable paths")).toHaveCount(0);
  await page.getByLabel("Validation commands").fill("pnpm check");
  await expect(page.getByLabel("Executable paths")).toHaveCount(0);
  await page.getByRole("tab", { name: "Info", exact: true }).click();
  await page.getByRole("tab", { name: "Integration", exact: true }).click();
  await expect(page.getByLabel("Validation commands")).toHaveValue(
    "pnpm check",
  );
  await page.getByRole("button", { name: "Save integration settings" }).click();
  await expect(selection).toHaveCount(0);
  await page.reload();
  await expect(selection).toHaveCount(0);

  await page.screenshot({ path: "/tmp/flowfield-slice3-local.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: "/tmp/flowfield-slice3-local-mobile.png" });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
