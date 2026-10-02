import { mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { join, resolve } from "node:path";
import { execFileSync } from "node:child_process";
import {
  test,
  expect,
  type Page,
  type APIRequestContext,
} from "@playwright/test";

function existingDirectory(path: string) {
  mkdirSync(path, { recursive: true });
  return path;
}

test("board failures retain readable context and recover through their alert", async ({
  page,
  request,
}) => {
  expect(
    (
      await request.post("/api/projects/initialize", {
        data: {
          path: existingDirectory(join(state, "board-recovery")),
          task_prefix: "BRC",
        },
      })
    ).ok(),
  ).toBe(true);
  expect(
    (
      await request.post("/api/projects/board-recovery/tasks", {
        data: { title: "Preserved board work" },
      })
    ).ok(),
  ).toBe(true);
  let fail = true;
  await page.route("**/api/projects/board-recovery/view/board", (route) =>
    fail
      ? route.fulfill({
          status: 503,
          json: {
            error: { code: "unavailable", message: "Temporary board failure" },
          },
        })
      : route.continue(),
  );
  await page.goto("/projects/board-recovery");
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("Board unavailable");
  await expect(alert).toContainText("Temporary board failure");
  fail = false;
  await alert.getByRole("button", { name: "Retry board" }).click();
  const card = page.getByRole("link", { name: /Preserved board work/ });
  await expect(card).toBeVisible();
  await expect(alert).toHaveCount(0);
  await expect(page.getByText("Connected", { exact: true })).toBeVisible();
  fail = true;
  expect(
    (
      await request.post("/api/projects/board-recovery/tasks", {
        data: { title: "New work after recovery" },
      })
    ).ok(),
  ).toBe(true);
  await expect(alert).toContainText("Could not refresh the board");
  await expect(card).toBeVisible();
  fail = false;
  await alert.getByRole("button", { name: "Refresh board" }).click();
  await expect(
    page.getByRole("link", { name: /New work after recovery/ }),
  ).toBeVisible();
  await expect(alert).toHaveCount(0);
});

const state = process.env.FLOWFIELD_SMOKE_STATE!;
const checkout = resolve("..");
// Catalog discovery is a control-plane read; ordinary browser checks never start Codex.
test.beforeEach(async ({ page }) => {
  await page.route("**/api/worker-models", (route) =>
    route.fulfill({ json: [] }),
  );
});
function cli(args: string[], cwd?: string) {
  return JSON.parse(
    execFileSync(
      "uv",
      [
        "run",
        "--project",
        checkout,
        "flowfield",
        "--data-dir",
        state,
        "--port",
        "8766",
        ...args,
        "--json",
      ],
      { encoding: "utf8", cwd },
    ),
  );
}

async function closeOverlay(page: Page) {
  for (let depth = 0; depth < 6; depth++) {
    const dialog = page
      .locator("[data-slot=dialog-content][data-state=open]:not(.suspended)")
      .last();
    if (!(await dialog.count())) return;
    const url = page.url();
    try {
      await dialog.locator(".close-control").first().click({ timeout: 1000 });
    } catch (error) {
      // Archive/Restore may finish closing while the click waits for stability.
      if (await dialog.count()) throw error;
    }
    await page.evaluate(() => new Promise(requestAnimationFrame));
    await expect
      .poll(async () => page.url() !== url || (await dialog.count()) === 0)
      .toBe(true);
  }
  await expect(
    page.locator("[data-slot=dialog-content][data-state=open]"),
  ).toHaveCount(0);
}

async function askWorker(page: Page) {
  const trigger = page.getByRole("button", { name: "Ask worker", exact: true });
  const input = page.getByLabel("Message the worker", { exact: true });
  await expect(trigger.or(input).first()).toBeVisible();
  if (await trigger.isVisible()) await trigger.click();
}

async function ensureEditing(page: Page) {
  const button = page.getByRole("button", { name: "Edit", exact: true });
  const field = page
    .getByLabel("Title", { exact: true })
    .or(page.getByLabel("Description", { exact: true }));
  await expect(button.or(field).first()).toBeAttached();
  if (await button.isVisible()) await button.click();
}

async function connectMcp(request: APIRequestContext) {
  const headers = { Accept: "application/json, text/event-stream" };
  const initialized = await request.post("/mcp/", {
    headers,
    data: {
      jsonrpc: "2.0",
      id: 1,
      method: "initialize",
      params: {
        protocolVersion: "2025-11-25",
        capabilities: {},
        clientInfo: { name: "browser-test", version: "1" },
      },
    },
  });
  expect(initialized.ok()).toBe(true);
  const protocol = (await initialized.json()).result.protocolVersion;
  const connectedHeaders = { ...headers, "MCP-Protocol-Version": protocol };
  await request.post("/mcp/", {
    headers: connectedHeaders,
    data: { jsonrpc: "2.0", method: "notifications/initialized" },
  });
  return async (name: string, args: Record<string, unknown>) => {
    const response = await request.post("/mcp/", {
      headers: connectedHeaders,
      data: {
        jsonrpc: "2.0",
        id: 2,
        method: "tools/call",
        params: { name, arguments: args },
      },
    });
    expect(response.ok()).toBe(true);
    const result = (await response.json()).result;
    expect(result.isError).toBe(false);
    return result.structuredContent;
  };
}

function resultMessage(v: {
  id: string;
  version: number;
  revision: number;
  created_at: string;
  report: { summary: string } | null;
}) {
  return {
    id: "result:" + v.id,
    kind: "result",
    source_id: v.id,
    revision: v.revision,
    created_at: v.created_at,
    author: "service",
    actor: { role: "flowfield", label: "Flowfield", identity: "service" },
    stages: [],
    stage_changes: [],
    state: { label: "Recorded", tone: "complete", href: null },
    title: "Result " + v.version,
    body: v.report?.summary ?? "",
    truncated: false,
    status: null,
    result_id: v.id,
    run_id: null,
  };
}
test("three-task board across CLI, browser and MCP, with archive and mobile reading", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const project = join(state, "harbor");
  cli(["project", "init", existingDirectory(project), "--name", "Harbor"]);
  cli([
    "project",
    "edit",
    "--project",
    "harbor",
    "--description",
    "Make expense reporting effortless",
  ]);
  cli([
    "milestone",
    "create",
    "--project",
    "harbor",
    "--id",
    "csv",
    "--title",
    "CSV export",
  ]);
  for (const [id, title, type] of [
    ["serializer", "Build CSV serializer", "feature"],
    ["button", "Add download button", "feature"],
    ["rounding", "Fix total rounding", "bug"],
  ]) {
    cli([
      "task",
      "create",
      "--project",
      "harbor",
      "--id",
      id,
      "--title",
      title,
      "--type",
      type,
      "--status",
      "up-next",
      "--body",
      "Verification passes",
      ...(id === "rounding" ? [] : ["--milestone", "csv"]),
    ]);
  }
  await page.goto("/projects/harbor");
  await expect(
    page.getByRole("heading", { name: "Harbor", level: 1, exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Ship CSV export", { exact: true })).toHaveCount(
    0,
  );
  const call = await connectMcp(request);
  const rounding = page
    .locator(".card-shell")
    .filter({ hasText: "Fix total rounding" });
  const backlog = page.getByRole("region", {
    name: "Backlog",
    exact: true,
    includeHidden: true,
  });
  const upNext = page.getByRole("region", {
    name: "Up next",
    exact: true,
    includeHidden: true,
  });
  await rounding.dragTo(backlog);
  await expect(backlog).toContainText("Fix total rounding");
  expect(
    (await call("get_board", { project_id: "harbor" })).columns
      .flatMap((c: { tasks: { id: string; status: string }[] }) => c.tasks)
      .find((t: { id: string }) => t.id === "rounding").status,
  ).toBe("backlog");
  // A keyboard/touch alternative shares the same operation.
  await page.getByRole("link", { name: /Fix total rounding/ }).click();
  await page
    .getByRole("button", { name: "Choose for Up next", exact: true })
    .focus();
  await page.keyboard.press("Enter");
  await expect(upNext).toContainText("Fix total rounding");
  await closeOverlay(page);
  await rounding.dragTo(
    page.getByRole("region", {
      name: "In progress",
      exact: true,
      includeHidden: true,
    }),
  );
  await expect(upNext).toContainText("Fix total rounding");
  const current = await call("get_task", {
    project_id: "harbor",
    task_id: "rounding",
  });
  await call("record_progress", {
    project_id: "harbor",
    task_id: "rounding",
    progress: { expected_revision: current.revision, status: "in_progress" },
  });
  await expect(
    page.getByRole("region", {
      name: "In progress",
      exact: true,
      includeHidden: true,
    }),
  ).toContainText("Fix total rounding");
  await expect(rounding).toHaveAttribute("draggable", "false");
  await page.getByRole("link", { name: /Fix total rounding/ }).click();
  await expect(
    page
      .getByRole("dialog")
      .getByRole("button", { name: "Archive", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Move task", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Close editor" }).click();
  await page
    .locator(".card-shell")
    .filter({ hasText: "Add download button" })
    .dragTo(
      page.locator(".card-shell").filter({ hasText: "Build CSV serializer" }),
      { targetPosition: { x: 15, y: 10 } },
    );
  await expect(
    page
      .getByRole("region", {
        name: "Up next",
        exact: true,
        includeHidden: true,
      })
      .locator(".task-card")
      .first(),
  ).toContainText("Add download button");
  await page.getByRole("link", { name: /Add download button/ }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Archive", exact: true })
    .click();
  const archiveConfirmation = page.getByRole("alertdialog");
  await expect(archiveConfirmation).toBeVisible();
  await expect(
    archiveConfirmation.getByRole("button", { name: "Cancel", exact: true }),
  ).toBeFocused();
  await archiveConfirmation
    .getByRole("button", { name: "Cancel", exact: true })
    .click();
  expect(cli(["task", "show", "button", "--project", "harbor"]).archived).toBe(
    false,
  );
  await page.getByRole("button", { name: "Archive", exact: true }).click();
  await archiveConfirmation
    .getByRole("button", { name: "Archive", exact: true })
    .click();
  await expect(
    page
      .getByRole("region", {
        name: "Up next",
        exact: true,
        includeHidden: true,
      })
      .locator(".task-card"),
  ).toHaveCount(1);
  expect(cli(["task", "show", "button", "--project", "harbor"]).status).toBe(
    "up_next",
  );
  await closeOverlay(page);
  await page.getByRole("button", { name: /^Archive \d/ }).click();
  await expect(
    page.getByRole("link", { name: /Add download button/ }),
  ).toBeVisible();
  await expect(page.locator(".column")).toHaveCount(0);
  await page.getByRole("link", { name: /Add download button/ }).click();
  await page.getByRole("button", { name: "Restore", exact: true }).click();
  await closeOverlay(page);
  await page.getByRole("button", { name: /^Board \d/ }).click();
  await page.getByLabel("Filter by milestone").selectOption("csv");
  await expect(page.locator(".task-card")).toHaveCount(2);
  await page
    .getByRole("button", { name: "Edit milestone", exact: true })
    .click();
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await page
    .getByRole("textbox", { name: "Description", exact: true })
    .fill("Export filtered expenses without losing row order.");
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await page.getByRole("button", { name: "Close editor" }).click();
  await closeOverlay(page);
  await page.getByRole("button", { name: /^Board / }).click();
  await page.getByLabel("Filter by milestone").selectOption("");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("link", { name: /Build CSV serializer/ }).click();
  await expect(page.locator(".task-definition")).toContainText(
    "Verification passes",
  );
  await expect(
    page.getByRole("button", { name: "Edit", exact: true }),
  ).toHaveCount(0);

  expect(errors).toEqual([]);
});

test("coordinator updates refresh the agreement while project editing retains conflict protection", async ({
  page,
  request,
}) => {
  const call = await connectMcp(request);
  await call("initialize_project", {
    project_id: "live",
    path: existingDirectory(join(state, "live")),
    name: "Live project",
  });
  await call("create_task", {
    project_id: "live",
    task: { id: "one", title: "First task", body: "First description" },
  });
  await page.goto("/projects/live");
  await page.getByRole("link", { name: /First task/ }).click();
  await expect(page.locator(".task-definition")).toContainText(
    "First description",
  );
  await call("edit_task", {
    project_id: "live",
    task_id: "one",
    changes: { expected_revision: 1, body: "Agent update" },
  });
  await expect(page.locator(".task-definition")).toContainText("Agent update");
  await closeOverlay(page);
  await page
    .getByRole("button", { name: "Project details", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "Edit project", exact: true }),
  ).toBeVisible();
  await ensureEditing(page);
  await page
    .getByRole("textbox", { name: "Description", exact: true })
    .fill("Unsaved focus");
  await call("edit_project", {
    project_id: "live",
    changes: { expected_revision: 1, description: "Agent description" },
  });
  await expect(page.getByText(/A newer revision is available/)).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "Description", exact: true }),
  ).toHaveValue("Unsaved focus");
  await page.getByRole("button", { name: "Close editor", exact: true }).click();
  await page
    .getByRole("button", { name: "Discard changes", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Project details", exact: true })
    .click();
  await ensureEditing(page);
  await page
    .getByRole("textbox", { name: "Description", exact: true })
    .fill("Stale project draft");
  await call("edit_project", {
    project_id: "live",
    changes: {
      expected_revision: 2,
      description: "Another coordinator update",
    },
  });
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("stale");
});

test("CLI adoption updates the browser, task creation and initial connection recovery", async ({
  page,
  request,
}) => {
  await page.route("**/api/projects", (route) => route.abort());
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "Retry connection" }),
  ).toBeVisible();
  await page.unroute("**/api/projects");
  await page.getByRole("button", { name: "Retry connection" }).click();
  await expect(
    page.getByRole("region", { name: "Project setup instructions" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Add project", exact: true }),
  ).toHaveCount(0);
  const adopted = cli([
    "project",
    "init",
    existingDirectory(join(state, "browser")),
  ]);
  await page.getByRole("link", { name: adopted.name, exact: true }).click();
  await expect(page).toHaveURL("/projects/" + adopted.id);
  await page.getByRole("button", { name: "New task", exact: true }).click();
  await page
    .getByRole("textbox", { name: "Title", exact: true })
    .fill("Investigate large exports");
  await page
    .getByRole("combobox", { name: "Type", exact: true })
    .selectOption("investigation");
  await page.getByRole("button", { name: "Create task", exact: true }).click();
  await expect(
    page
      .getByRole("region", {
        name: "Backlog",
        exact: true,
        includeHidden: true,
      })
      .getByRole("link", {
        name: /Investigate large exports/,
        includeHidden: true,
      }),
  ).toBeVisible();
  const tasks = await (await request.get("/api/projects/browser/tasks")).json();
  expect(tasks[0].task_type).toBe("investigation");
  expect(tasks[0].id).toMatch(/^investigate-large-exports-/);
  await page.reload();
  await expect(
    page.getByRole("link", {
      name: /Investigate large exports/,
      includeHidden: true,
    }),
  ).toBeVisible();
});

test("priority races preserve agent progress and message drafts; touch can prioritize", async ({
  page,
  request,
  browser,
}) => {
  const call = await connectMcp(request);
  await call("initialize_project", {
    project_id: "race",
    path: existingDirectory(join(state, "race")),
  });
  await call("create_task", {
    project_id: "race",
    task: { id: "one", title: "Race task", body: "Original" },
  });
  await page.goto("/projects/race");
  await page.getByRole("link", { name: /Race task/ }).click();
  await askWorker(page);
  await page
    .getByLabel("Message the worker", { exact: true })
    .fill("Keep this unsaved requirement");
  const priorityPage = await page.context().newPage();
  await priorityPage.goto("/projects/race");
  // Hold the real priority request while the coordinator starts work.
  let release!: () => void;
  let received!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const started = new Promise<void>((resolve) => {
    received = resolve;
  });
  await priorityPage.route("**/tasks/one/prioritize", async (route) => {
    received();
    await gate;
    await route.continue();
  });
  await priorityPage
    .locator(".card-shell")
    .filter({ hasText: "Race task" })
    .dragTo(
      priorityPage.getByRole("region", {
        name: "Up next",
        exact: true,
        includeHidden: true,
      }),
    );
  await started;
  await call("record_progress", {
    project_id: "race",
    task_id: "one",
    progress: { expected_revision: 1, status: "in_progress" },
  });
  release();
  await expect(priorityPage.getByRole("alert")).toContainText("stale");
  await expect(
    page.getByRole("region", {
      name: "In progress",
      exact: true,
      includeHidden: true,
    }),
  ).toContainText("Race task");
  await expect(
    page.getByLabel("Message the worker", { exact: true }),
  ).toHaveValue("Keep this unsaved requirement");
  await priorityPage.unrouteAll({ behavior: "wait" });
  await priorityPage.close();
  await call("create_task", {
    project_id: "race",
    task: { id: "touch", title: "Touch task" },
  });
  const context = await browser.newContext({
    hasTouch: true,
    viewport: { width: 390, height: 844 },
  });
  const touch = await context.newPage();
  await touch.goto("http://127.0.0.1:8766/projects/race");
  await touch.getByRole("link", { name: /Touch task/ }).tap();
  await touch
    .getByRole("button", { name: "Choose for Up next", exact: true })
    .tap();
  await expect(
    touch.getByRole("region", {
      name: "Up next",
      exact: true,
      includeHidden: true,
    }),
  ).toContainText("Touch task");
  await context.close();
});

test("Conversation permalinks preserve message drafts while coordinator revisions update", async ({
  page,
  request,
}) => {
  const revisionReads: string[] = [];
  page.on("request", (req) => {
    if (req.url().includes("/revisions/")) revisionReads.push(req.url());
  });
  const call = await connectMcp(request);
  await call("initialize_project", {
    project_id: "tabs",
    path: existingDirectory(join(state, "tabs")),
  });
  await call("create_task", {
    project_id: "tabs",
    task: { id: "one", title: "Tabbed task", body: "Original description" },
  });
  await page.goto("/projects/tabs");
  await page.getByRole("link", { name: /Tabbed task/ }).click();
  await expect(
    page.getByRole("tab", { name: "Task", exact: true }),
  ).toHaveCount(0);
  await askWorker(page);
  await page
    .getByLabel("Message the worker", { exact: true })
    .fill("Unsaved draft");
  const history = page.getByRole("list", { name: "Task feed" });
  await history
    .getByRole("link", { name: "Permalink: Task defined", exact: true })
    .focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/conversation\/definition%3A1$/);
  await call("edit_task", {
    project_id: "tabs",
    task_id: "one",
    changes: { expected_revision: 1, body: "New saved context" },
  });
  await expect(history).toContainText("Task definition updated");
  expect(revisionReads).toHaveLength(0);
  await history.locator(".task-changes").first().locator("summary").click();
  await expect(
    history
      .locator(".change-after")
      .getByText("New saved context", { exact: true }),
  ).toBeVisible();
  expect(revisionReads).toHaveLength(2);
  await expect(history).not.toContainText("Unsaved draft");
  await expect(
    page.getByLabel("Message the worker", { exact: true }),
  ).toHaveValue("Unsaved draft");
  await expect(page.locator(".task-definition")).toContainText(
    "New saved context",
  );
  await expect(
    page.getByRole("button", { name: "Load latest", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText("The context changed.", { exact: false }),
  ).toBeVisible();
});

test("archive preserves dated records and supports restore and mobile archiving", async ({
  page,
  request,
}) => {
  const call = await connectMcp(request);
  await call("initialize_project", {
    project_id: "archive-list",
    path: existingDirectory(join(state, "archive-list")),
  });
  await call("create_milestone", {
    project_id: "archive-list",
    milestone: { id: "csv", title: "Exports" },
  });
  for (const [id, status, milestone_id] of [
    ["older", "up_next", "csv"],
    ["newer", "done", null],
  ]) {
    await call("create_task", {
      project_id: "archive-list",
      task: {
        id,
        title: `${id} task`,
        status: status === "done" ? "backlog" : status,
        milestone_id,
      },
    });
    if (status === "done")
      await call("record_progress", {
        project_id: "archive-list",
        task_id: id,
        progress: {
          expected_revision: 1,
          status: "done",
          completion: "report",
        },
      });
    await call("edit_task", {
      project_id: "archive-list",
      task_id: id,
      changes: { expected_revision: status === "done" ? 2 : 1, archived: true },
    });
  }
  // Later text edits do not change when the older task was archived.
  await call("edit_task", {
    project_id: "archive-list",
    task_id: "older",
    changes: { expected_revision: 2, body: "Edited after archiving" },
  });
  await page.goto("/projects/archive-list");
  await expect(
    page.getByRole("button", { name: "New task", exact: true }),
  ).toBeVisible();
  await closeOverlay(page);
  await page.getByRole("button", { name: /^Archive \d/ }).click();
  const archive = page.getByRole("region", {
    name: "Archived tasks",
    exact: true,
    includeHidden: true,
  });
  await expect(page.locator(".column")).toHaveCount(0);
  await expect(archive.getByRole("listitem")).toHaveCount(2);
  await expect(archive.getByRole("listitem").first()).toContainText(
    "newer task",
  );
  await expect(
    archive.getByRole("listitem").first().locator(".record-attribution time"),
  ).toBeVisible();
  await page.getByLabel("Filter by milestone").selectOption("csv");
  await expect(archive.getByRole("listitem")).toHaveCount(1);
  await page.getByLabel("Filter by milestone").selectOption("");
  await archive.getByRole("link", { name: /older task/ }).click();
  const info = page.getByRole("dialog", { name: "Task details", exact: true });
  await expect(
    info.getByRole("heading", { name: /Up next|Archived/ }),
  ).toHaveCount(0);
  await expect(info.getByText(/Previously|Your agent/)).toHaveCount(0);
  const actions = page.getByRole("dialog", {
    name: "Task details",
    exact: true,
  });
  await expect(
    actions.getByRole("button", { name: "Edit", exact: true }),
  ).toHaveCount(0);
  await expect(
    actions.getByRole("button", { name: "Restore", exact: true }),
  ).toBeEnabled();
  await actions.getByRole("button", { name: "Restore", exact: true }).click();
  await expect(archive.getByRole("listitem")).toHaveCount(1);
  expect(
    (await call("get_task", { project_id: "archive-list", task_id: "older" }))
      .status,
  ).toBe("up_next");
  await closeOverlay(page);
  await page.getByRole("button", { name: /^Board \d/ }).click();
  await page.getByRole("link", { name: /older task/ }).click();
  await page.setViewportSize({ width: 390, height: 844 });

  await actions.getByRole("button", { name: "Archive", exact: true }).click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Archive", exact: true })
    .click();
  await closeOverlay(page);
  await page.getByRole("button", { name: /^Archive \d/ }).click();
  await expect(archive.getByRole("listitem").first()).toContainText(
    "older task",
  );
});

test("coordinator dependencies update blockers, links and reconciliation without browser editing", async ({
  page,
  request,
}) => {
  const call = await connectMcp(request);
  const identity = { project_id: "dependencies" };
  await call("initialize_project", {
    ...identity,
    path: existingDirectory(join(state, "dependencies")),
  });
  const one = await call("create_task", {
    ...identity,
    task: {
      id: "serializer",
      title: "Serialize CSV",
      body: "Deliver CSV",
      status: "up_next",
    },
  });
  const two = await call("create_task", {
    ...identity,
    task: {
      id: "download",
      title: "Download CSV",
      body: "Offer download",
      status: "up_next",
    },
  });
  await page.goto("/projects/dependencies/tasks/DEP-2");
  const three = await call("create_task", {
    ...identity,
    task: {
      title: "Prepare export settings",
      body: "Choose settings",
      status: "up_next",
    },
  });
  await call("edit_task", {
    ...identity,
    task_id: two.id,
    changes: {
      expected_revision: two.revision,
      dependencies: [one.id, three.id],
    },
  });
  const detail = page.getByRole("dialog", {
    name: "Task details",
    exact: true,
  });
  await expect(
    detail.getByText(/^Waiting on (DEP-1, DEP-3|DEP-3, DEP-1)$/),
  ).toBeVisible();
  await expect(
    detail.getByText("Waiting on prerequisites", { exact: true }),
  ).toHaveCount(0);
  await expect(detail.locator(".task-needs")).toHaveCount(0);
  await detail.getByRole("link", { name: "DEP-3", exact: true }).hover();
  await expect(page.getByRole("tooltip")).toHaveText("Prepare export settings");
  await expect(detail.getByLabel("Add prerequisite")).toHaveCount(0);
  await detail
    .locator(".work-state")
    .getByRole("link", { name: "DEP-1", exact: true })
    .click();
  await expect(page).toHaveURL(/DEP-1$/);
  await page.getByText("Related work", { exact: true }).click();
  await expect(
    detail.getByRole("link", { name: "DEP-2", exact: true }),
  ).toBeVisible();
  await page.goBack();
  await call("record_progress", {
    ...identity,
    task_id: one.id,
    progress: { expected_revision: 1, status: "done", completion: "report" },
  });
  await expect(
    detail.getByText("Needs code from DEP-1", { exact: true }),
  ).toBeVisible();
  await expect(
    detail.getByText("Waiting on DEP-3", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Close editor", exact: true }).click();
  const card = page.locator(".task-card").filter({ hasText: "Download CSV" });
  await expect(
    card.getByText("Waiting on DEP-3", { exact: true }),
  ).toBeVisible();
  await expect(
    card.getByRole("link", { name: "DEP-3", exact: true }),
  ).toHaveCount(1);
  await expect(
    card.getByText("Needs code from DEP-1", { exact: true }),
  ).toBeVisible();
});

test("task keys link to persistent selections with native tabs, history and draft protection", async ({
  page,
  request,
}) => {
  cli([
    "project",
    "init",
    existingDirectory(join(state, "navigation")),
    "--name",
    "Navigation trial",
  ]);
  cli([
    "project",
    "init",
    existingDirectory(join(state, "nav-other")),
    "--name",
    "Other navigation",
    "--prefix",
    "NVO",
  ]);
  const call = await connectMcp(request);
  const one = await call("create_task", {
    project_id: "navigation",
    task: { title: "Prepare the export", status: "up_next" },
  });
  const two = await call("create_task", {
    project_id: "navigation",
    task: {
      title: "Download the export",
      status: "up_next",
      dependencies: [one.key],
    },
  });
  const old = await call("create_task", {
    project_id: "navigation",
    task: { title: "Earlier experiment" },
  });
  await call("edit_task", {
    project_id: "navigation",
    task_id: old.key,
    changes: { expected_revision: 1, archived: true },
  });
  expect([one.key, two.key, old.key]).toEqual(["NAV-1", "NAV-2", "NAV-3"]);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/projects/navigation");
  const blocker = page
    .locator(".work-state")
    .getByRole("link", { name: "NAV-1", exact: true });
  await blocker.hover();
  await expect(page.getByRole("tooltip")).toHaveText("Prepare the export");
  await page.keyboard.press("Escape");
  await expect(blocker).toHaveAttribute(
    "href",
    "/projects/navigation/tasks/NAV-1",
  );
  await expect(
    page.getByText("Completion criteria set", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText("Add completion criteria", { exact: true }),
  ).toHaveCount(0);
  await blocker.click();
  await expect(page).toHaveURL(/tasks\/NAV-1$/);
  const editor = page.getByRole("dialog", {
    name: "Task details",
    exact: true,
  });
  await expect(
    page.getByRole("dialog").getByRole("heading", { name: /NAV-1/ }),
  ).toContainText("NAV-1");
  await page.reload();
  await expect(
    page
      .getByRole("dialog")
      .getByRole("heading", { name: / Prepare the export$/ }),
  ).toBeVisible();
  await askWorker(page);
  await editor
    .getByLabel("Message the worker", { exact: true })
    .fill("Unsaved title");
  await page.goBack();
  await page.getByRole("button", { name: "Keep editing", exact: true }).click();
  await expect(page).toHaveURL(/tasks\/NAV-1$/);
  await expect(
    editor.getByLabel("Message the worker", { exact: true }),
  ).toHaveValue("Unsaved title");
  await page.goBack();
  await page
    .getByRole("button", { name: "Discard changes", exact: true })
    .click();
  await expect(page).toHaveURL(/projects\/navigation$/);
  await expect(editor).toHaveCount(0);
  await page.goForward();
  await expect(
    page
      .getByRole("dialog")
      .getByRole("heading", { name: / Prepare the export$/ }),
  ).toBeVisible();
  await closeOverlay(page);
  await page
    .getByRole("link", {
      name: /NAV-2 (?:Feature (?:DRAFT )?)?Download the export/,
    })
    .click();
  const prerequisite = editor
    .locator(".work-state")
    .getByRole("link", { name: "NAV-1", exact: true });
  const [tab] = await Promise.all([
    page.context().waitForEvent("page"),
    prerequisite.click({ button: "middle" }),
  ]);
  await tab.waitForLoadState("domcontentloaded");
  await expect(tab).toHaveURL(/tasks\/NAV-1$/);
  await expect(
    tab.getByRole("heading", {
      name: "NAV-1 · Prepare the export",
      exact: true,
    }),
  ).toBeVisible();
  await tab.close();
  await expect(page).toHaveURL(/tasks\/NAV-2(?:\/dependencies)?$/);
  await prerequisite.click();
  await expect(page).toHaveURL(/tasks\/NAV-1$/);
  await page.goBack();
  await expect(page).toHaveURL(/tasks\/NAV-2(?:\/dependencies)?$/);
  await expect(
    page
      .getByRole("dialog")
      .getByRole("heading", { name: / Download the export$/ }),
  ).toBeVisible();
  await closeOverlay(page);
  await page
    .getByRole("link", { name: "Other navigation", exact: true })
    .click();
  await expect(page).toHaveURL(/projects\/nav-other$/);
  await page.goBack();
  await expect(page).toHaveURL(/projects\/navigation$/);
  await page
    .getByRole("link", {
      name: /NAV-2 (?:Feature (?:DRAFT )?)?Download the export/,
    })
    .click();
  await expect(
    page
      .getByRole("dialog")
      .getByRole("heading", { name: / Download the export$/ }),
  ).toBeVisible();
  const renamed = await call("edit_task", {
    project_id: "navigation",
    task_id: one.key,
    changes: {
      expected_revision: 1,
      title: "Prepare the corrected export",
      task_type: "bug",
    },
  });
  expect(renamed.key).toBe("NAV-1");
  await prerequisite.hover();
  await expect(page.getByRole("tooltip")).toHaveText(
    "Prepare the corrected export",
  );
  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 390, height: 844 });

  await page.goto("/projects/navigation/tasks/NAV-3");
  await expect(page).toHaveURL(/tasks\/NAV-3$/);
  await expect(
    page.getByRole("region", { name: "Archived tasks", includeHidden: true }),
  ).toBeVisible();
  await expect(
    page
      .getByRole("dialog")
      .getByRole("heading", { name: / Earlier experiment$/ }),
  ).toBeVisible();
  await page.reload();
  await expect(
    editor.getByRole("button", { name: "Restore", exact: true }),
  ).toBeVisible();
  await page.goto("/projects/navigation/tasks/NAV-999");
  await expect(
    page.getByRole("heading", { name: "Task not found", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "New task", exact: true }),
  ).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("three-letter prefix setup, coordinator dependencies and immediate accessible tooltips", async ({
  page,
  request,
}) => {
  const call = await connectMcp(request);
  const adopted = cli([
    "project",
    "init",
    existingDirectory(join(state, "detail-settings")),
    "--name",
    "Detail settings",
    "--prefix",
    "UIX",
  ]);
  await page.goto("/projects/" + adopted.id);
  await page
    .getByRole("button", { name: "Project details", exact: true })
    .click();
  await ensureEditing(page);
  await expect(page.getByLabel("Directory", { exact: true })).toHaveCount(0);
  await page.getByLabel("Prefix", { exact: true }).fill("UIT");
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Edit", exact: true }),
  ).toBeVisible();
  await ensureEditing(page);
  await expect(page.getByLabel("Prefix", { exact: true })).toHaveValue("UIT");
  const identity = { project_id: "detail-settings" };
  const one = await call("create_task", {
    ...identity,
    task: {
      title:
        "Build a reliable serializer with a deliberately long descriptive title",
      status: "up_next",
    },
  });
  expect(one.key).toBe("UIT-1");
  await expect(page.getByLabel("Prefix", { exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Close editor", exact: true }).click();
  const card = page.getByRole("link", {
    name: /UIT-1 (?:Feature (?:DRAFT )?)?Build a reliable/,
  });
  await expect(card).not.toHaveAttribute("title");
  await card.hover();
  const tooltip = page.getByRole("tooltip");
  await expect(tooltip).toHaveCount(0);
  await card.focus();
  await expect(tooltip).toHaveCount(0);
  await card.click();
  const editor = page.getByRole("dialog", {
    name: "Task details",
    exact: true,
  });
  const info = editor;
  await expect(info.getByLabel("Add prerequisite")).toHaveCount(0);
  await expect(
    editor.getByText("No prerequisites.", { exact: true }),
  ).toHaveCount(0);
  const prerequisite = await call("create_task", {
    ...identity,
    task: { title: "Provide export fixtures", status: "up_next" },
  });
  const dependencies = editor;
  await call("edit_task", {
    ...identity,
    task_id: one.id,
    changes: {
      expected_revision: one.revision,
      dependencies: [prerequisite.id],
      body: "Keep the agreed scope.",
    },
  });
  await expect(
    dependencies.getByText("Waiting on UIT-2", { exact: true }),
  ).toBeVisible();
  await call("record_progress", {
    ...identity,
    task_id: prerequisite.key,
    progress: { expected_revision: 1, status: "done", completion: "report" },
  });
  await expect(
    dependencies.getByText("Needs code from UIT-2", { exact: true }),
  ).toBeVisible();
  await page.setViewportSize({ width: 1600, height: 1000 });

  const keyLink = dependencies
    .locator(".task-needs")
    .getByRole("link", { name: prerequisite.key, exact: true });
  await keyLink.hover();
  await expect(tooltip).toHaveText("Provide export fixtures");

  await page.setViewportSize({ width: 390, height: 844 });
  await editor.scrollIntoViewIfNeeded();
  await keyLink.hover();
  await expect(tooltip).toBeVisible();

  const mcpProject = await call("initialize_project", {
    project_id: "prefix-transport",
    path: existingDirectory(join(state, "prefix-transport")),
    task_prefix: "mcp",
  });
  expect(mcpProject.task_prefix).toBe("MCP");
  const cliProject = cli([
    "project",
    "edit",
    "--project",
    "prefix-transport",
    "--prefix",
    "CLX",
  ]);
  expect(cliProject.task_prefix).toBe("CLX");
  const reinitialized = await call("initialize_project", {
    project_id: "prefix-transport",
    path: join(state, "prefix-transport"),
    task_prefix: "clx",
  });
  expect(reinitialized.task_prefix).toBe("CLX");
});

test("task activity, Markdown and independently retrievable decisions preserve the current agreement", async ({
  page,
  request,
}) => {
  const call = await connectMcp(request);
  const identity = { project_id: "activity-trial" };
  await call("initialize_project", {
    ...identity,
    path: existingDirectory(join(state, "activity-trial")),
    task_prefix: "ACT",
  });
  const task = await call("create_task", {
    ...identity,
    task: {
      title: "Download CSV",
      body: "Export filtered rows.\n\n## Done when\n\n- [ ] Preserve row order\n- [x] Quote commas",
    },
  });
  await page.goto("/projects/activity-trial/tasks/ACT-1");
  const editor = page.getByRole("dialog", {
    name: "Task details",
    exact: true,
  });
  const activity = editor.getByRole("list", { name: "Task feed" });
  await expect(
    page.locator(".task-definition").getByRole("checkbox"),
  ).toHaveCount(2);
  await expect(
    editor.getByRole("button", { name: "Add note", exact: true }),
  ).toHaveCount(0);
  await expect(
    editor.getByRole("button", { name: "Add decision", exact: true }),
  ).toHaveCount(0);
  await call("add_activity", {
    ...identity,
    entry: {
      task_id: task.key,
      body: "## Finding\n\nReuse **serializer**.\n\n[Unsafe](javascript:alert(1))\n<script>window.evil = true</script>",
    },
  });
  await expect(
    activity.getByRole("heading", { name: "Finding", exact: true }),
  ).toBeVisible();
  await expect(activity.locator('a[href^="javascript:"]')).toHaveCount(0);
  await expect(activity.locator("script")).toHaveCount(0);
  const old = await call("add_activity", {
    ...identity,
    entry: {
      task_id: task.key,
      kind: "decision",
      body: "Visible columns only",
    },
  });
  await call("add_activity", {
    ...identity,
    entry: {
      task_id: task.key,
      kind: "decision",
      body: "Include hidden columns but exclude secrets.",
      supersedes: old.id,
    },
  });
  await expect(
    activity.getByRole("img", { name: "Superseded", exact: true }),
  ).toBeVisible();
  expect(
    (await call("get_task", { ...identity, task_id: task.key })).body,
  ).toBe(
    "Export filtered rows.\n\n## Done when\n\n- [ ] Preserve row order\n- [x] Quote commas",
  );
  await closeOverlay(page);
  // CLI project decisions are visible in a distinct project-scoped view, including on refresh.
  const projectDecision = cli([
    "project",
    "decide",
    "--project",
    identity.project_id,
    "--body",
    "All exports must work **offline**.",
  ]);
  await page.getByRole("button", { name: "Decisions", exact: true }).click();
  const projectView = page.getByRole("region", {
    name: "Project decisions",
    exact: true,
    includeHidden: true,
  });
  await expect(projectView.locator(".decision-cards")).toContainText(
    "All exports must work offline.",
  );
  await expect(projectView.locator(".decision-cards")).not.toContainText(
    "Include hidden columns",
  );
  await page.getByRole("button", { name: "New decision", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByLabel("Decision and rationale", { exact: true })
    .fill("Reuse existing UI components to keep the product consistent.");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Keep editing", exact: true }).click();
  await expect(
    page
      .getByRole("dialog")
      .getByLabel("Decision and rationale", { exact: true }),
  ).toHaveValue("Reuse existing UI components to keep the product consistent.");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Save decision", exact: true })
    .click();
  await expect(projectView.locator(".decision-cards > li")).toHaveCount(2);
  await page.reload();
  await expect(projectView).toBeVisible();
  expect(
    cli(["project", "decisions", "--project", identity.project_id]).items[1].id,
  ).toBe(projectDecision.id);
  const note = cli([
    "task",
    "note",
    "ACT-1",
    "--project",
    identity.project_id,
    "--body",
    "Serializer verified; download integration remains.",
  ]);
  expect(
    cli(["task", "activity", "ACT-1", "--project", identity.project_id])
      .items[0].id,
  ).toBe(note.id);
  await page.goto("/projects/activity-trial/tasks/ACT-1");
  await expect(activity).toContainText(
    "Serializer verified; download integration remains.",
  );
});

test("readable conversation preserves message drafts, revision links and legacy paths", async ({
  page,
  request,
}) => {
  await request.post("/api/projects/initialize", {
    data: {
      path: existingDirectory(join(state, "readable")),
      task_prefix: "REA",
    },
  });
  await request.post("/api/projects/readable/tasks", {
    data: {
      title: "Readable agreement",
      body: "## Outcome\n\nExport **all columns**.\n\n## Done when\n\n- [ ] Keep row order",
    },
  });
  await page.goto("/projects/readable/tasks/REA-1");
  const editor = page.getByRole("dialog", {
    name: "Task details",
    exact: true,
  });
  const thread = editor.getByRole("list", { name: "Task feed" });
  await expect(
    thread.getByRole("img", { name: "Recorded", exact: true }),
  ).toBeVisible();
  await expect(thread).toContainText("You");
  await expect(page.locator(".task-definition .markdown strong")).toHaveText(
    "all columns",
  );
  await expect(
    page.locator(".task-definition").getByRole("checkbox"),
  ).toBeDisabled();
  await expect(editor.getByRole("textbox")).toHaveCount(0);
  await askWorker(page);
  await expect(editor.getByRole("textbox")).toHaveCount(1);
  await askWorker(page);
  await editor
    .getByLabel("Message the worker", { exact: true })
    .fill("Export **selected columns**.");
  await thread
    .getByRole("link", { name: "Permalink: Task defined", exact: true })
    .click();
  await page.goBack();
  await expect(
    editor.getByLabel("Message the worker", { exact: true }),
  ).toHaveValue("Export **selected columns**.");
  const current = await (
    await request.get("/api/projects/readable/tasks/REA-1")
  ).json();
  await request.put("/api/projects/readable/tasks/REA-1", {
    data: {
      expected_revision: current.revision,
      body: "Export **selected columns**.",
    },
  });
  await thread
    .getByRole("link", {
      name: "Permalink: Task definition updated",
      exact: true,
    })
    .click();
  await expect(page).toHaveURL(/conversation\/definition%3A2$/);
  await thread.locator(".task-changes summary").click();
  await expect(thread.locator(".change-before")).toContainText("all columns");
  await expect(thread.locator(".change-after")).toContainText(
    "selected columns",
  );
  const url = page.url();
  await page.reload();
  const other = await page.context().newPage();
  await other.goto(url);
  await expect(other.locator('[data-message-id="definition:2"]')).toBeVisible();
  await other.close();
  await page.goto("/projects/readable/tasks/REA-1/history");
  await expect(thread).toBeVisible();
  await expect(editor.getByRole("tab")).toHaveCount(0);
});

test("conversation pages exact revisions without hiding notes or superseded decisions", async ({
  page,
  request,
}) => {
  await request.post("/api/projects/initialize", {
    data: {
      path: existingDirectory(join(state, "edit-feed")),
      task_prefix: "EDF",
    },
  });
  const path = "/api/projects/edit-feed";
  await request.post(`${path}/tasks`, {
    data: { id: "one", title: "Edit feed", body: "Original agreement" },
  });
  for (let revision = 1; revision <= 32; revision++) {
    const response = await request.put(`${path}/tasks/one`, {
      data: {
        expected_revision: revision,
        body: `Description ${revision}`,
        author: "agent",
      },
    });
    expect(response.ok()).toBe(true);
  }
  await request.post(`${path}/activity`, {
    data: { task_id: "one", body: "Pause to discuss scope." },
  });
  await request.put(`${path}/tasks/one`, {
    data: { expected_revision: 33, body: "After discussion", author: "agent" },
  });
  const old = await (
    await request.post(`${path}/activity`, {
      data: { task_id: "one", kind: "decision", body: "Old choice" },
    })
  ).json();
  await request.post(`${path}/activity`, {
    data: {
      task_id: "one",
      kind: "decision",
      body: "Current choice",
      supersedes: old.id,
    },
  });
  await page.goto("/projects/edit-feed/tasks/EDF-1/activity");
  const activity = page.getByRole("list", { name: "Task feed" });
  await expect(activity).toContainText("Current choice");
  await expect(
    activity.getByRole("img", { name: "Superseded", exact: true }),
  ).toBeVisible();
  await expect(activity).toContainText("Pause to discuss scope.");
  await page
    .getByRole("button", { name: "Load earlier activity", exact: true })
    .click();
  await expect(activity.locator('[data-kind="definition"]')).toHaveCount(34);
  await expect(
    page.getByRole("button", { name: "Load earlier activity", exact: true }),
  ).toHaveCount(0);
  const second = activity.locator('[data-message-id="definition:2"]');
  await second.locator(".task-changes summary").click();
  await expect(second.locator(".change-before")).toContainText(
    "Original agreement",
  );
  await expect(second.locator(".change-after")).toContainText("Description 1");
});

test("question-only cards omit empty needs while project questions remain visible", async ({
  page,
  request,
}) => {
  const call = await connectMcp(request);
  const project_id = "compact-needs";
  await call("initialize_project", {
    project_id,
    path: existingDirectory(join(state, project_id)),
    task_prefix: "CMP",
  });
  await call("create_task", {
    project_id,
    task: { id: "one", title: "One task" },
  });
  await call("ask_question", {
    project_id,
    question: {
      id: "task-question",
      task_id: "CMP-1",
      question: "Which behavior?",
      context: "Choose the outcome",
      recommendation: "Keep it small",
      blocking_scope: "Task outcome",
    },
  });
  await page.goto(`/projects/${project_id}`);
  const card = page.locator(".task-card");
  await expect(
    card.getByRole("link", { name: "Needs your answer", exact: true }),
  ).toBeVisible();
  await expect(card.locator(".task-needs")).toHaveCount(0);
  await call("ask_question", {
    project_id,
    question: {
      id: "project-question",
      affected_task_ids: ["CMP-1"],
      question: "Which project constraint?",
      context: "Shared scope",
      recommendation: "Preserve existing behavior",
      blocking_scope: "Project constraint",
    },
  });
  await expect(
    card
      .locator(".task-needs")
      .getByRole("link", { name: "Which project constraint?", exact: true }),
  ).toBeVisible();
});

test("Needs you carries a free-text answer from browser to coordinator application", async ({
  page,
  request,
}) => {
  const call = await connectMcp(request);
  const project_id = "inbox-trial";
  await call("initialize_project", {
    project_id,
    path: existingDirectory(join(state, project_id)),
    task_prefix: "ASK",
  });
  await call("create_task", {
    project_id,
    task: { id: "serializer", title: "Serialize CSV", status: "up_next" },
  });
  await call("create_task", {
    project_id,
    task: {
      id: "download",
      title: "Download CSV",
      body: "Export CSV",
      status: "up_next",
      dependencies: ["ASK-1"],
    },
  });
  const question = await call("ask_question", {
    project_id,
    question: {
      id: "export-scope",
      task_id: "ASK-2",
      question: "What should the export include?",
      context: "The table is paginated.",
      recommendation: "Export all filtered rows, capped at 10,000.",
      choices: ["All filtered rows", "Visible page"],
      blocking_scope: "Choosing the export contract",
    },
  });
  expect(cli(["inbox", "list", "--project", project_id]).needs_you_count).toBe(
    1,
  );
  await page.goto(`/projects/${project_id}`);
  // Hiding a repeated question must not leave an empty needs list on any card.
  await expect(
    page.getByRole("link", { name: "Needs your answer", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".task-card .task-needs:empty")).toHaveCount(0);
  await page
    .getByRole("link", { name: "Needs your answer", exact: true })
    .click();
  await expect(page).toHaveURL(/conversation\/question:export-scope:1$/);
  await expect(page.getByRole("list", { name: "Task feed" })).toBeVisible();
  const questionEntry = page.locator(
    '.conversation-message[data-kind="question"]',
  );
  await expect(questionEntry.locator(".detail-title")).toHaveText("Question");
  await expect(
    questionEntry.locator(".conversation-message-body strong"),
  ).toHaveText("What should the export include?");
  await questionEntry.getByRole("img", { name: "Needs your answer" }).focus();
  await expect(page.getByRole("tooltip")).toHaveText("Needs your answer");
  await page.getByLabel("Your answer", { exact: true }).focus();
  await page.keyboard.press("Escape");
  await closeOverlay(page);
  await page.getByRole("button", { name: /^Needs you(?: \d+)?$/ }).click();
  await page
    .getByRole("link", { name: /What should the export include/ })
    .click();

  await expect(
    page
      .getByRole("region", {
        name: "Needs your action",
        exact: true,
        includeHidden: true,
      })
      .locator(".attention-card"),
  ).toHaveCount(1);
  await expect(
    page
      .getByRole("region", {
        name: "Waiting",
        exact: true,
        includeHidden: true,
      })
      .locator(".attention-card"),
  ).toHaveCount(0);
  await expect(
    page.getByRole("region", {
      name: "History",
      exact: true,
      includeHidden: true,
    }),
  ).toHaveCount(1);
  await page.reload();
  const detail = page.getByRole("dialog", {
    name: "Task details",
    exact: true,
  });
  await expect(
    detail
      .locator(".conversation-message-body strong")
      .filter({ hasText: "What should the export include?" }),
  ).toBeVisible();
  await expect(detail.getByRole("form", { name: "Task input" })).toContainText(
    "What should the export include?",
  );
  await expect(
    detail.getByRole("button", { name: "All filtered rows", exact: true }),
  ).toHaveCount(0);
  await detail
    .getByLabel("Your answer", { exact: true })
    .fill(
      "All filtered rows, capped at 10,000. Explain when the limit is exceeded.",
    );
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Keep editing", exact: true }).click();
  await expect(detail.getByLabel("Your answer", { exact: true })).toHaveValue(
    /Explain/,
  );
  await detail
    .getByRole("button", { name: "Send answer", exact: true })
    .click();
  await expect(detail.getByText("Answer sent.", { exact: true })).toBeVisible();
  await expect(
    page
      .getByRole("region", {
        name: "Needs your action",
        exact: true,
        includeHidden: true,
      })
      .locator(".attention-card"),
  ).toHaveCount(0);
  await expect(
    page
      .getByRole("region", {
        name: "Waiting",
        exact: true,
        includeHidden: true,
      })
      .locator(".attention-card"),
  ).toHaveCount(1);
  const answered = await call("get_question", {
    project_id,
    question_id: question.id,
  });
  expect(answered.status).toBe("answered");
  expect((await call("get_task", { project_id, task_id: "ASK-2" })).body).toBe(
    "Export CSV",
  );
  expect(
    (
      await request.post(`/api/projects/${project_id}/tasks/ASK-2/progress`, {
        data: { expected_revision: 1, status: "in_progress" },
      })
    ).status(),
  ).toBe(409);

  await call("apply_answer", {
    project_id,
    question_id: question.id,
    application: {
      expected_revision: answered.revision,
      expected_task_revision: 1,
      body: "Export all filtered rows, capped at 10,000. Explain when the limit is exceeded.",
      decision:
        "Export all filtered rows with a 10,000-row cap and an explicit limit message.",
    },
  });
  await expect(detail.locator('[data-kind="activity"]')).toContainText(
    "10,000-row cap",
  );
  const applied = await call("get_task", { project_id, task_id: "ASK-2" });
  expect(applied.blocking_question_count).toBe(0);
  expect(applied.blocked_by[0].key).toBe("ASK-1");
  expect(applied.status).toBe("up_next");
  await expect(page.getByLabel("Show resolved")).toHaveCount(0);
  await expect(page.locator(".attention-card")).toHaveCount(1);
  await page.setViewportSize({ width: 390, height: 844 });
  await detail.scrollIntoViewIfNeeded();

  await expect(page.locator('[data-kind="activity"]')).toContainText(
    "10,000-row cap",
  );
  await expect(
    page
      .locator('[data-kind="activity"]')
      .getByRole("link", { name: "Related question", exact: true }),
  ).toHaveAttribute(
    "href",
    `/projects/${project_id}/tasks/ASK-2/conversation/question%3Aexport-scope%3A1`,
  );
});

test("Needs you preserves conflicting drafts and follows up on the same canonical question", async ({
  page,
  request,
}) => {
  const call = await connectMcp(request);
  const project_id = "inbox-conflict";
  await call("initialize_project", {
    project_id,
    path: existingDirectory(join(state, project_id)),
    task_prefix: "CNF",
  });
  await call("create_task", {
    project_id,
    task: { id: "one", title: "Export" },
  });
  const q = cli([
    "inbox",
    "ask",
    "--affects",
    "CNF-1",
    "--project",
    project_id,
    "--question",
    "What cap?",
    "--context",
    "Large exports need a limit.",
    "--recommendation",
    "Use 10,000 rows.",
    "--blocking-scope",
    "Export limit",
  ]);
  await page.goto(`/projects/${project_id}/inbox/${q.id}`);
  const detail = page.getByRole("dialog", { name: "Question", exact: true });
  await detail
    .getByLabel("Your answer", { exact: true })
    .fill("My unsaved custom answer");
  cli([
    "inbox",
    "answer",
    q.id,
    "--project",
    project_id,
    "--answer",
    "Cap it at 10,000.",
  ]);
  await expect(
    detail.getByText("This question changed while you were answering."),
  ).toBeVisible();
  await expect(detail.getByLabel("Your answer", { exact: true })).toHaveValue(
    "My unsaved custom answer",
  );
  await detail
    .getByRole("button", { name: "Send answer", exact: true })
    .click();
  await expect(detail.getByRole("alert")).toContainText("stale");
  page.once("dialog", (d) => d.accept());
  await detail
    .getByRole("button", { name: "Load latest", exact: true })
    .click();
  await expect(
    detail.getByText("Cap it at 10,000.", { exact: true }),
  ).toBeVisible();
  await call("follow_up_question", {
    project_id,
    question_id: q.id,
    follow_up: {
      expected_revision: 2,
      question: "What happens above the cap?",
      context: "Truncate or reject?",
      recommendation: "Reject and explain the limit.",
    },
  });
  await expect(
    detail.getByRole("heading", {
      name: "What happens above the cap?",
      exact: true,
    }),
  ).toBeVisible();
  await detail.getByText("Earlier responses", { exact: true }).click();
  await expect(detail.locator(".answer-history")).toContainText(
    "Cap it at 10,000.",
  );
  await expect(page.locator(".attention-card")).toHaveCount(1);
  await detail
    .getByLabel("Your answer", { exact: true })
    .fill("Reject, explain the cap, and suggest narrowing filters.");
  await detail
    .getByRole("button", { name: "Send answer", exact: true })
    .click();
  await expect(detail.getByRole("status")).toContainText("Resume coordinator");
  const reply = cli(["inbox", "show", q.id, "--project", project_id]);
  expect(reply.answer).toContain("narrowing filters");
  expect(reply.revision).toBe(4);
  const withdrawal = cli([
    "inbox",
    "withdraw",
    q.id,
    "--project",
    project_id,
    "--reason",
    "Export postponed.",
  ]);
  expect(withdrawal.status).toBe("withdrawn");
  await expect(
    detail.getByRole("heading", { name: "Reason withdrawn", exact: true }),
  ).toBeVisible();
});

test("project decision cards preserve deep links, drafts and replacement history", async ({
  page,
  request,
}) => {
  const call = await connectMcp(request);
  const project_id = "decision-cards";
  await call("initialize_project", {
    project_id,
    path: existingDirectory(join(state, project_id)),
    task_prefix: "DEC",
  });
  const original = cli([
    "project",
    "decide",
    "--project",
    project_id,
    "--body",
    "Keep exports offline. [Guide](https://example.test/guide)\n\n" +
      "Supporting rationale. ".repeat(100),
  ]);
  await page.goto(`/projects/${project_id}/decisions/${original.id}`);
  const detail = page.getByRole("dialog", { name: "Decision", exact: true });
  await expect(detail).toContainText("Supporting rationale.");
  await expect(detail.getByText("Current", { exact: true })).toHaveCount(0);
  await expect(
    detail.getByRole("link", { name: "Guide", exact: true }),
  ).toHaveAttribute("href", "https://example.test/guide");
  await expect(page.locator(".decision-cards a a")).toHaveCount(0);
  await expect(page.getByLabel("Decision filter")).toHaveCount(0);
  await page.reload();
  await detail
    .getByRole("button", { name: "Replace decision", exact: true })
    .click();
  const draft = detail.getByLabel("Decision and rationale");
  await draft.fill("Allow online exports when the user opts in.");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Keep editing", exact: true }).click();
  await expect(draft).toHaveValue(
    "Allow online exports when the user opts in.",
  );
  const competing = cli([
    "project",
    "decide",
    "--project",
    project_id,
    "--body",
    "Keep exports offline for the MVP.",
    "--supersedes",
    original.id,
  ]);
  await expect(detail.getByText("Superseded", { exact: true })).toHaveCount(0);
  await expect(
    detail.getByRole("button", { name: "Save replacement", exact: true }),
  ).toBeDisabled();
  await expect(draft).toHaveValue(
    "Allow online exports when the user opts in.",
  );
  page.once("dialog", (d) => d.accept());
  await detail.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(detail.getByText("Superseded", { exact: true })).toBeVisible();
  await detail.getByText("Replacement decision", { exact: true }).click();
  await expect(detail).toContainText("Keep exports offline for the MVP.");
  await expect(
    page.getByRole("link", { name: "History", exact: true }),
  ).toHaveCount(0);
  await expect(page.locator(".decision-cards > li")).toHaveCount(1);
  await page.goto(`/projects/${project_id}/decisions/${competing.id}`);
  await detail
    .getByRole("button", { name: "Replace decision", exact: true })
    .click();
  await draft.fill("Allow exports with an explicit online opt-in.");
  await detail
    .getByRole("button", { name: "Save replacement", exact: true })
    .click();
  await expect(page).not.toHaveURL(new RegExp(`${competing.id}$`));
  await expect(detail).toContainText(
    "Allow exports with an explicit online opt-in.",
  );
  await expect(page.locator(".decision-cards > li")).toHaveCount(1);
  await detail.getByText("Earlier decision", { exact: true }).click();
  await expect(detail).toContainText("Keep exports offline for the MVP.");
  await page.reload();
  await expect(detail).toContainText(
    "Allow exports with an explicit online opt-in.",
  );
  await page.setViewportSize({ width: 390, height: 844 });

  for (let i = 0; i < 22; i++) {
    const result = await request.post(`/api/projects/${project_id}/activity`, {
      data: {
        id: `additional-${i}`,
        kind: "decision",
        body: `Independent decision ${i}`,
        author: "agent",
      },
    });
    expect(result.ok()).toBe(true);
  }
  await page.reload();
  await expect(page.locator(".decision-cards > li")).toHaveCount(20);
  await closeOverlay(page);
  await page.getByRole("button", { name: "Load older", exact: true }).click();
  await expect(page.locator(".decision-cards > li")).toHaveCount(23);
  await page
    .locator(".decision-cards")
    .getByText("Allow exports with an explicit online opt-in.", { exact: true })
    .click();
  await expect(detail).toContainText(
    "Allow exports with an explicit online opt-in.",
  );
});

test("retracting answers reopens questions and withdrawing decisions preserves their rationale", async ({
  page,
  request,
}) => {
  const call = await connectMcp(request);
  const project_id = "reversible";
  await call("initialize_project", {
    project_id,
    path: existingDirectory(join(state, project_id)),
    task_prefix: "REV",
  });
  await call("create_task", {
    project_id,
    task: { id: "export", title: "Export" },
  });
  await call("ask_question", {
    project_id,
    question: {
      id: "scope",
      affected_task_ids: ["REV-1"],
      question: "Which rows?",
      context: "Choose export scope",
      recommendation: "All rows",
      blocking_scope: "Export contract",
    },
  });
  await call("answer_question", {
    project_id,
    question_id: "scope",
    response: { expected_revision: 1, answer: "Visible page" },
  });
  await page.goto(`/projects/${project_id}/inbox/scope`);
  const detail = page.getByRole("dialog", { name: "Question", exact: true });
  await expect(
    detail.getByRole("link", { name: "Which rows?", exact: true }),
  ).toHaveAttribute("href", `/projects/${project_id}/inbox/scope`);
  await expect(
    detail.getByRole("button", { name: "Retract", exact: true }),
  ).toHaveCount(0);
  expect(
    (
      await request.post(
        `/api/projects/${project_id}/questions/scope/retract-answer`,
        { data: { expected_revision: 2 } },
      )
    ).ok(),
  ).toBe(true);
  await expect(detail.getByRole("status")).toHaveText("Needs your answer");
  await expect(
    page
      .getByRole("region", {
        name: "Needs your action",
        exact: true,
        includeHidden: true,
      })
      .locator(".attention-card"),
  ).toHaveCount(1);
  await detail.getByText("Earlier responses", { exact: true }).click();
  await expect(detail).toContainText("Visible page");
  const question = await call("get_question", {
    project_id,
    question_id: "scope",
  });
  expect(question.answer).toBeNull();
  expect(question.revision).toBe(3);
  expect(
    (await call("get_task", { project_id, task_id: "REV-1" }))
      .blocking_question_count,
  ).toBe(1);
  await detail
    .getByRole("button", { name: "Close question", exact: true })
    .click();
  await expect(page).toHaveURL(new RegExp(`/projects/${project_id}/inbox$`));
  const decision = cli([
    "project",
    "decide",
    "--project",
    project_id,
    "--body",
    "Offline exports only",
  ]);
  await page.goto(`/projects/${project_id}/decisions/${decision.id}`);
  const decisionDetail = page.getByRole("dialog", {
    name: "Decision",
    exact: true,
  });
  await decisionDetail
    .getByRole("button", { name: "Withdraw", exact: true })
    .click();
  await decisionDetail
    .getByLabel("Reason for withdrawal")
    .fill("The requirement no longer applies");
  await decisionDetail
    .getByRole("button", { name: "Withdraw", exact: true })
    .click();
  await expect(
    decisionDetail.getByText("Withdrawn", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".decision-cards > li")).toHaveCount(0);
  await expect(
    page.getByRole("link", { name: "History", exact: true }),
  ).toHaveCount(0);
  await decisionDetail.getByText("Withdrawal", { exact: true }).click();
  await expect(decisionDetail).toContainText(
    "The requirement no longer applies",
  );
  await page.reload();
  await expect(decisionDetail).toContainText("Offline exports only");
  await expect(
    decisionDetail.getByText("Withdrawn", { exact: true }),
  ).toBeVisible();
});

test("milestones group tasks with linked details and long project intent stays out of the shell", async ({
  page,
  request,
}) => {
  const call = await connectMcp(request);
  const project_id = "milestone-trial";
  await call("initialize_project", {
    project_id,
    path: existingDirectory(join(state, project_id)),
    task_prefix: "MIL",
  });
  const description = "Enduring project intent. ".repeat(500);
  await call("edit_project", {
    project_id,
    changes: { expected_revision: 1, description },
  });
  const milestone = await call("create_milestone", {
    project_id,
    milestone: {
      id: "export",
      title: "CSV export",
      body: "Deliver a useful export.",
    },
  });
  expect(milestone.key).toBe("M-1");
  expect(
    (await call("get_milestone", { project_id, milestone_id: "M-1" })).id,
  ).toBe("export");
  expect(cli(["milestone", "show", "M-1", "--project", project_id]).key).toBe(
    "M-1",
  );
  await call("create_task", {
    project_id,
    task: {
      id: "serializer",
      title: "Serialize CSV",
      milestone_id: "M-1",
    },
  });
  await call("record_progress", {
    project_id,
    task_id: "serializer",
    progress: { expected_revision: 1, status: "done", completion: "report" },
  });
  await call("create_task", {
    project_id,
    task: { id: "download", title: "Download CSV", milestone_id: "M-1" },
  });
  await call("create_task", {
    project_id,
    task: { id: "unrelated", title: "Independent bug", task_type: "bug" },
  });
  await page.goto(`/projects/${project_id}`);
  await expect(page.getByText(description, { exact: true })).toHaveCount(0);

  const badge = page
    .locator(".task-card")
    .filter({ hasText: "Download CSV" })
    .getByRole("link", { name: "M-1", exact: true });
  await badge.hover();
  await expect(page.getByRole("tooltip")).toHaveText("CSV export");
  await badge.focus();
  await expect(page.getByRole("tooltip")).toHaveText("CSV export");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  await badge.click();
  await expect(page).toHaveURL(new RegExp(`/milestones/M-1$`));
  await expect(page.locator(".board")).toBeVisible();
  const editor = page.getByRole("region", {
    name: "Edit milestone",
    exact: true,
  });
  await expect(editor).toContainText("Deliver a useful export.");

  await editor.getByRole("button", { name: "Edit", exact: true }).click();
  await editor
    .getByLabel("Description", { exact: true })
    .fill("Preserved milestone draft");
  await expect(page.getByRole("tab")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Keep editing", exact: true }).click();
  await expect(editor.getByLabel("Description", { exact: true })).toHaveValue(
    "Preserved milestone draft",
  );
  await editor
    .getByRole("button", { name: "Save changes", exact: true })
    .click();
  await page.reload();
  await expect(editor).toContainText("Preserved milestone draft");
  await page.goto(`/projects/${project_id}/milestones/export`);
  await expect(editor).toContainText("Preserved milestone draft");
  // Historical tab URLs remain valid aliases of the simplified detail.
  await page.goto(`/projects/${project_id}/milestones/export/tasks`);
  await expect(editor).toContainText("Preserved milestone draft");
  await page.getByRole("button", { name: "View tasks", exact: true }).click();
  await expect(page).toHaveURL(`/projects/${project_id}`);
  await expect(page.getByLabel("Filter by milestone")).toHaveValue("export");
  await expect(page.locator(".task-card")).toHaveCount(2);
  await expect(page.locator(".board")).toContainText("Serialize CSV");
  await expect(page.locator(".board")).not.toContainText("Independent bug");
  await page
    .getByRole("button", { name: "Project details", exact: true })
    .click();
  const project = page.getByRole("region", {
    name: "Edit project",
    exact: true,
  });
  await expect(project.getByText(description, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Close editor", exact: true }).click();
  await expect(page.getByText(description, { exact: true })).toHaveCount(0);
});

test("collections open and close entity details through the same routes", async ({
  page,
  request,
}) => {
  const call = await connectMcp(request);
  const project_id = "collection-layout";
  await call("initialize_project", {
    project_id,
    path: existingDirectory(join(state, project_id)),
    task_prefix: "COL",
  });
  await call("create_milestone", {
    project_id,
    milestone: { id: "group", title: "Delivery" },
  });
  await call("create_task", {
    project_id,
    task: { id: "retired", title: "Retired work" },
  });
  await call("edit_task", {
    project_id,
    task_id: "retired",
    changes: { expected_revision: 1, archived: true },
  });
  await request.post(`/api/projects/${project_id}/activity`, {
    data: {
      id: "choice",
      kind: "decision",
      body: "Work offline",
      author: "human",
    },
  });

  for (const path of ["milestones", "decisions", "archive"]) {
    await page.goto(`/projects/${project_id}/${path}`);
    const row = page.locator(".collection-row").first();
    await expect(row).toBeVisible();

    await row.click();
    await expect(page.locator(".entity-overlay")).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(
      page.locator("[data-slot=dialog-content][data-state=open]"),
    ).toHaveCount(0);
  }
});

test("task changes refresh only their project, without reloading the project catalog", async ({
  page,
  request,
}) => {
  for (const id of ["scope-one", "scope-two"]) {
    expect(
      (
        await request.post("/api/projects/initialize", {
          data: {
            id,
            path: existingDirectory(join(state, id)),
            task_prefix: id === "scope-one" ? "SOA" : "SOB",
          },
        })
      ).ok(),
    ).toBe(true);
    expect(
      (
        await request.post(`/api/projects/${id}/tasks`, {
          data: { id: "one", title: "Original" },
        })
      ).ok(),
    ).toBe(true);
  }
  await page.goto("/projects/scope-one");
  await expect(page.getByRole("link", { name: /Original/ })).toBeVisible();
  await expect(page.getByText("Connected", { exact: true })).toBeVisible();
  // Register a second listener before writing so the test knows the event arrived.
  await page.evaluate(
    () =>
      new Promise<void>((ready) => {
        const events = new EventSource("/api/events");
        (window as unknown as { scopedChange: Promise<void> }).scopedChange =
          new Promise<void>((resolve) => {
            events.addEventListener("change", (event) => {
              if (JSON.parse(event.data).projects === null) ready();
              if (JSON.parse(event.data).projects?.includes("scope-two")) {
                events.close();
                resolve();
              }
            });
          });
      }),
  );
  const reads: string[] = [];
  page.on("request", (req) => {
    if (
      req.method() === "GET" &&
      /\/api\/projects(?:$|\/scope-one\/view\/board$)/.test(req.url())
    )
      reads.push(req.url());
  });
  expect(
    (
      await request.put("/api/projects/scope-two/tasks/one", {
        data: { expected_revision: 1, title: "Other project change" },
      })
    ).ok(),
  ).toBe(true);
  await page.evaluate(
    () => (window as unknown as { scopedChange: Promise<void> }).scopedChange,
  );
  expect(
    (
      await request.put("/api/projects/scope-one/tasks/one", {
        data: { expected_revision: 1, title: "Relevant change" },
      })
    ).ok(),
  ).toBe(true);
  await expect(
    page.getByRole("link", { name: /Relevant change/ }),
  ).toBeVisible();
  expect(reads).toHaveLength(1);
  expect(reads[0]).toContain("scope-one/view/board");
});

test("project questions link affected tasks and reconcile through CLI and MCP", async ({
  page,
  request,
}) => {
  const call = await connectMcp(request);
  const project_id = "project-input";
  await call("initialize_project", {
    project_id,
    path: existingDirectory(join(state, project_id)),
    task_prefix: "PRJ",
  });
  await call("create_task", {
    project_id,
    task: { id: "first", title: "Catalog", status: "up_next" },
  });
  await call("create_task", {
    project_id,
    task: {
      id: "second",
      title: "Report",
      status: "up_next",
      dependencies: ["PRJ-1"],
    },
  });
  const q = cli([
    "inbox",
    "ask",
    "--project",
    project_id,
    "--question",
    "Which output convention?",
    "--context",
    "Both exports should agree.",
    "--recommendation",
    "UTF-8",
    "--affects",
    "PRJ-2",
    "--blocking-scope",
    "Output contract",
  ]);
  expect(q.task_id).toBeNull();
  expect(
    (await call("list_questions", { project_id, task_id: "PRJ-2" })).items[0]
      .id,
  ).toBe(q.id);
  await page.goto(`/projects/${project_id}/inbox/${q.id}`);
  const detail = page.getByRole("dialog", { name: "Question", exact: true });
  await expect(
    detail.getByRole("link", { name: "Project", exact: true }),
  ).toHaveAttribute("href", `/projects/${project_id}`);
  await expect(
    detail.getByRole("link", { name: "PRJ-2", exact: true }),
  ).toHaveAttribute("href", `/projects/${project_id}/tasks/PRJ-2`);
  await detail.getByRole("link", { name: "PRJ-2", exact: true }).hover();
  await expect(page.getByRole("tooltip")).toHaveText("Report");
  await detail
    .getByLabel("Your answer", { exact: true })
    .fill("UTF-8, without a byte-order mark.");
  await detail
    .getByRole("button", { name: "Send answer", exact: true })
    .click();
  await expect(detail.getByRole("status")).toHaveText("Resume coordinator");
  const resumed = await connectMcp(request);
  const briefing = await resumed("get_board", { project_id });
  expect(briefing.recommendation.action).toBe("apply_answer");
  expect(briefing.recommendation.id).toBe(q.id);
  expect(briefing.attention.answered.items[0].id).toBe(q.id);
  const pending = await resumed("get_question", {
    project_id,
    question_id: q.id,
  });
  expect(
    (
      await request.post(
        `/api/projects/${project_id}/questions/${q.id}/retract-answer`,
        { data: { expected_revision: pending.revision } },
      )
    ).ok(),
  ).toBe(true);
  await expect(detail.getByRole("status")).toHaveText("Needs your answer");
  await detail
    .getByLabel("Your answer", { exact: true })
    .fill("UTF-8, without a byte-order mark.");
  await detail
    .getByRole("button", { name: "Send answer", exact: true })
    .click();
  await expect(detail.getByRole("status")).toHaveText("Resume coordinator");

  const result = cli([
    "inbox",
    "apply",
    q.id,
    "--project",
    project_id,
    "--description",
    "Offline UTF-8 outputs, without BOM.",
    "--decision",
    "Exports use UTF-8 without BOM; the Report task uses the project convention.",
  ]);
  expect(result.applied_project_revision).toBe(2);
  expect(result.applied_task_revisions.second).toBe(1);
  await expect(detail.getByRole("status")).toHaveText("Answered");
  const task = await call("get_task", { project_id, task_id: "PRJ-2" });
  expect(task.blocking_question_count).toBe(0);
  expect(task.blocked_by[0].key).toBe("PRJ-1");
  const advisory = await call("ask_question", {
    project_id,
    question: {
      question: "Audience?",
      context: "Choose the project audience.",
      recommendation: "Personal use",
    },
  });
  await call("answer_question", {
    project_id,
    question_id: advisory.id,
    response: { expected_revision: 1, answer: "Personal use" },
  });
  await call("apply_answer", {
    project_id,
    question_id: advisory.id,
    application: {
      expected_revision: 2,
      expected_project_revision: 2,
      decision: "Target personal use.",
    },
  });
  await closeOverlay(page);
  await page.getByRole("button", { name: "Decisions", exact: true }).click();
  await expect(
    page.getByText("Target personal use.", { exact: true }),
  ).toBeVisible();
});

test("selected handoff survives a fresh MCP connection and shows stale context", async ({
  page,
  request,
}) => {
  const call = await connectMcp(request);
  const project_id = "handoff-trial";
  await call("initialize_project", {
    project_id,
    path: existingDirectory(join(state, project_id)),
    task_prefix: "HOF",
  });
  await call("create_task", {
    project_id,
    task: { id: "catalog", title: "Validate catalog", status: "in_progress" },
  });
  const checkpoint = cli([
    "task",
    "handoff",
    "HOF-1",
    "--project",
    project_id,
    "--expected-revision",
    "1",
    "--supersedes",
    "none",
    "--body",
    "catalog.py validates records. Checks: 4 unit tests pass. Next: inspect duplicate-ID handling.",
    "--author",
    "agent",
  ]);
  await call("add_activity", {
    project_id,
    entry: {
      task_id: "HOF-1",
      body: "Later note, not a continuation checkpoint.",
    },
  });
  const fresh = await connectMcp(request);
  const task = await fresh("get_task", { project_id, task_id: "HOF-1" });
  expect(task.handoff.id).toBe(checkpoint.id);
  expect(task.handoff.needs_recheck).toBe(false);
  await page.goto(`/projects/${project_id}/tasks/HOF-1/activity`);
  await expect(
    page.getByRole("img", { name: "Selected handoff", exact: true }),
  ).toBeVisible();
  await call("edit_task", {
    project_id,
    task_id: "HOF-1",
    changes: { expected_revision: 1, body: "Reject duplicate IDs." },
  });
  await expect(
    page.getByRole("img", {
      name: "Task changed · Recheck before continuing",
      exact: true,
    }),
  ).toBeVisible();
  expect(
    (await fresh("get_task", { project_id, task_id: "HOF-1" })).handoff
      .needs_recheck,
  ).toBe(true);
  await call("add_activity", {
    project_id,
    entry: {
      task_id: "HOF-1",
      kind: "handoff",
      expected_task_revision: 2,
      supersedes: checkpoint.id,
      body: "Rechecked duplicate IDs; next run the full suite.",
    },
  });
  await expect(
    page.getByRole("img", { name: "Selected handoff", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("img", { name: "Superseded", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Earlier handoff", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Replacement handoff", { exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByText("Rechecked duplicate IDs; next run the full suite.", {
      exact: true,
    }),
  ).toBeVisible();
});

test("publication shares browser, CLI and MCP state without manual editing", async ({
  page,
  request,
}) => {
  await request.post("/api/projects/initialize", {
    data: {
      path: existingDirectory(join(state, "publication")),
      task_prefix: "PRE",
    },
  });
  await request.post("/api/projects/publication/tasks", {
    data: {
      id: "export",
      title: "Export JSON",
      status: "up_next",
      body: "Export filtered rows as offline JSON. Test Unicode and empty results.",
    },
  });
  await page.goto("/projects/publication/tasks/PRE-1");
  const editor = page.getByRole("dialog", {
    name: "Task details",
    exact: true,
  });
  const draft = page.getByRole("dialog").getByText("Draft", { exact: true });
  await expect(draft).toBeVisible();
  await page.getByRole("dialog").getByText("Draft", { exact: true }).focus();
  await expect(page.getByRole("tooltip")).toContainText(
    "Resume your coordinator to prepare the agreed work.",
  );
  await page.keyboard.press("Escape");
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  await expect(editor).toBeVisible();
  await expect(
    editor.getByRole("button", { name: /publish|prepare/i }),
  ).toHaveCount(0);
  const first = cli([
    "task",
    "publish",
    "PRE-1",
    "--completion",
    "report",
    "--project",
    "publication",
  ]);
  expect(first.publication_status).toBe("published");
  await expect(draft).toHaveCount(0);
  const call = await connectMcp(request);
  await call("add_activity", {
    project_id: "publication",
    entry: { task_id: "PRE-1", body: "The sample has many Unicode titles." },
  });
  await expect(draft).toHaveCount(0);
  await call("edit_task", {
    project_id: "publication",
    task_id: "PRE-1",
    changes: {
      expected_revision: first.revision,
      body: "Export filtered rows as JSON, capped at 500. Test Unicode, empty output and overflow.",
    },
  });
  await call("add_activity", {
    project_id: "publication",
    entry: {
      kind: "decision",
      task_id: "PRE-1",
      body: "Keep exports offline.",
    },
  });
  await expect(draft).toBeVisible();
  const fromCli = cli([
    "task",
    "publish",
    "PRE-1",
    "--completion",
    "report",
    "--project",
    "publication",
  ]);
  expect(fromCli.publication_status).toBe("published");
  await expect(draft).toHaveCount(0);
  await call("add_activity", {
    project_id: "publication",
    entry: { kind: "decision", body: "Use UTF-8 for all exports." },
  });
  await expect(draft).toBeVisible();
  const current = await call("get_task", {
    project_id: "publication",
    task_id: "PRE-1",
  });
  await call("publish_task", {
    project_id: "publication",
    task_id: "PRE-1",
    publication: {
      completion: "report",
      expected_revision: current.revision,
      expected_decision_sequence: current.decision_sequence,
    },
  });
  await page.reload();
  await expect(draft).toHaveCount(0);
  await expect(editor).toContainText("capped at 500");
});

test("related questions preserve the page, drafts, focus and router history", async ({
  page,
  request,
}) => {
  const project_id = "overlays";
  await request.post("/api/projects/initialize", {
    data: {
      path: existingDirectory(join(state, project_id)),
      task_prefix: "OVR",
    },
  });
  const call = await connectMcp(request);
  await call("create_task", {
    project_id,
    task: {
      id: "export",
      title: "Export JSON",
      body: "Export selected books.",
      status: "up_next",
    },
  });
  const question = await call("ask_question", {
    project_id,
    question: {
      id: "scope",
      affected_task_ids: ["OVR-1"],
      question: "Which books?",
      context: "The assignment needs an export scope.",
      recommendation: "All filtered books.",
      blocking_scope: "Export scope",
    },
  });
  await page.goto(`/projects/${project_id}/tasks/OVR-1`);
  const boot = await page.evaluate(() => performance.timeOrigin);
  const editor = page.getByRole("dialog", {
    name: "Task details",
    exact: true,
  });
  const origin = `/projects/${project_id}/tasks/OVR-1`;
  await expect(
    editor.getByText("Needs your answer before work can continue:", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    editor.getByText("No dependency or input blockers.", { exact: true }),
  ).toHaveCount(0);
  const trigger = editor.locator(".task-needs").getByRole("link", {
    name: "Which books?",
    exact: true,
  });
  await page.setViewportSize({ width: 1280, height: 300 });
  await trigger.scrollIntoViewIfNeeded();
  const taskDialog = page.getByRole("dialog", {
    name: "Task details",
    exact: true,
  });
  const overlayScroll = await taskDialog
    .locator(".entity-overlay-body")
    .evaluate((el) => el.scrollTop);
  expect(overlayScroll).toBeGreaterThan(0);
  const scroll = await page.evaluate(() => scrollY);
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "Question", exact: true });
  await expect(dialog).toBeVisible();
  expect(await page.evaluate(() => scrollY)).toBe(scroll);
  await expect(page).toHaveURL(`${origin}/related/questions/${question.id}`);
  expect(await page.evaluate(() => performance.timeOrigin)).toBe(boot);
  const answer = dialog.getByLabel("Your answer", { exact: true });
  await answer.fill("My unsaved answer");
  await page.goBack();
  await page.getByRole("button", { name: "Keep editing", exact: true }).click();
  await expect(dialog).toBeVisible();
  await expect(answer).toHaveValue("My unsaved answer");
  await dialog
    .getByRole("button", { name: "Close question", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Discard changes", exact: true })
    .click();
  await expect(dialog).toHaveCount(0);
  await expect(page).toHaveURL(origin);
  await expect(trigger).toBeFocused();
  await expect
    .poll(() =>
      taskDialog.locator(".entity-overlay-body").evaluate((el) => el.scrollTop),
    )
    .toBe(overlayScroll);
  await trigger.click();
  await page.goBack();
  await expect(dialog).toHaveCount(0);
  await page.goForward();
  await expect(dialog).toBeVisible();
  await page.reload();
  await expect(dialog).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });

  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(origin);
  await page.goto(`${origin}/related/questions/${question.id}`);
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Your answer", { exact: true }).fill("All books.");
  await dialog
    .getByRole("button", { name: "Send answer", exact: true })
    .click();
  await expect(dialog.getByRole("status")).toHaveText("Resume coordinator");
  await dialog
    .getByRole("button", { name: "Close question", exact: true })
    .click();
  await expect(page).toHaveURL(origin);
  await expect(dialog).toHaveCount(0);
  await expect(
    editor.getByText("Resume your coordinator to use the saved answer:", {
      exact: true,
    }),
  ).toBeVisible();
  await closeOverlay(page);
  await page.getByRole("button", { name: /^Needs you(?: \d+)?$/ }).click();
  for (const name of ["Needs your action", "Waiting", "History"])
    await expect(page.getByRole("region", { name, exact: true })).toBeVisible();
  await expect(page.getByLabel("Show resolved")).toHaveCount(0);
});

test("task metadata and internal Markdown links stay inside the router", async ({
  page,
  request,
}) => {
  const project_id = "link-routing";
  await request.post("/api/projects/initialize", {
    data: {
      path: existingDirectory(join(state, project_id)),
      task_prefix: "LNK",
    },
  });
  const milestone = await request.post(
    `/api/projects/${project_id}/milestones`,
    { data: { id: "questions", title: "Export milestone" } },
  );
  expect(milestone.ok()).toBe(true);
  await request.post(`/api/projects/${project_id}/tasks`, {
    data: {
      id: "export",
      title: "Export books",
      milestone_id: "questions",
      body: `[Project decisions](/projects/${project_id}/decisions)`,
    },
  });
  await page.goto(`/projects/${project_id}/tasks/LNK-1`);
  const boot = await page.evaluate(() => performance.timeOrigin);
  const editor = page.getByRole("dialog", {
    name: "Task details",
    exact: true,
  });
  await editor
    .getByRole("link", { name: "Project decisions", exact: true })
    .click();
  await expect(page).toHaveURL(`/projects/${project_id}/decisions`);
  expect(await page.evaluate(() => performance.timeOrigin)).toBe(boot);
  await page.goBack();
  await editor.getByRole("link", { name: "M-1", exact: true }).click();
  await expect(page).toHaveURL(`/projects/${project_id}/milestones/M-1`);
  expect(await page.evaluate(() => performance.timeOrigin)).toBe(boot);
  await page.getByRole("button", { name: "View tasks", exact: true }).click();
  await expect(page).toHaveURL(`/projects/${project_id}`);
  await expect(page.getByLabel("Filter by milestone")).toHaveValue("questions");
  await expect(
    page.getByRole("link", { name: /LNK-1 (?:Feature )?Export books/ }),
  ).toBeVisible();
});

test("managed review keeps its URL, binds the result, and preserves feedback on conflict", async ({
  page,
}) => {
  cli([
    "project",
    "init",
    existingDirectory(join(state, "run-ui")),
    "--prefix",
    "RUN",
  ]);
  cli([
    "task",
    "create",
    "--project",
    "run-ui",
    "--id",
    "one",
    "--title",
    "Review a managed result",
    "--body",
    "Implement the behavior",
  ]);
  let run = {
    id: "attempt-one",
    project_id: "run-ui",
    task_id: "one",
    task_key: "RUN-1",
    revision: 7,
    status: "in_review",
    model: "fixture-model",
    effort: "low",
    created_at: "2026-01-01T10:00:00Z",
    started_at: "2026-01-01T10:00:00Z",
    ended_at: "2026-01-01T10:01:00Z",
    base_commit: "a".repeat(40),
    result_commit: "b".repeat(40),
    result: {
      summary: "Implemented the loader",
      checks: "10 fixture checks passed",
      limitations: "",
    },
    feedback: "",
    problem: null,
    code_available: false,
    usage: { total_tokens: 100, cached_input_tokens: 50, complete: true },
  };
  const version = () => ({
    id: run.id,
    project_id: run.project_id,
    task_id: run.task_id,
    task_key: run.task_key,
    version: 1,
    revision: run.revision,
    run_id: run.id,
    completion: "code",
    source_commit: run.result_commit,
    candidate_commit: run.result_commit,
    report: run.result,
    status: run.status === "in_review" ? "ready" : run.status,
    created_at: run.created_at,
    target_branch: "integration",
    integration_id: "check-one",
    feedback: run.status === "changes_requested" ? run.feedback : "",
    problem: run.problem,
  });
  let rejectOnce = true;
  let cancellations = 0;
  await page.route("**/api/projects/run-ui/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith("/thread"))
      return route.fulfill({
        json: { items: [resultMessage(version())], next_cursor: null },
      });
    if (path.includes("/thread/result"))
      return route.fulfill({ json: resultMessage(version()) });
    if (path.endsWith("/input-eligibility"))
      return route.fulfill({
        json: {
          enabled: true,
          reason: "idle",
          task_revision: 1,
          agreement_revision: 1,
          result_id: run.id,
          result_revision: run.revision,
          question_id: null,
          question_revision: null,
          run_id: null,
          pending_reply_id: null,
        },
      });
    if (path.endsWith("/replies")) {
      const body = route.request().postDataJSON();
      expect(body.binding.result_id).toBe(run.id);
      expect(body.action).toBe("changes");
      expect(body.binding.result_revision).toBe(7);
      if (rejectOnce) {
        rejectOnce = false;
        return route.fulfill({
          status: 409,
          json: {
            error: {
              code: "stale_revision",
              message: "Review changed; inspect the latest result.",
            },
          },
        });
      }
      run = {
        ...run,
        revision: 8,
        status: "changes_requested",
        feedback: body.body,
      };
      return route.fulfill({ json: version() });
    }
    if (path.endsWith("/location"))
      return route.fulfill({
        json: {
          workspace: "/fixture/worktree",
          diff_command: "git diff BASE RESULT",
          try_command: "Run fixture checks",
        },
      });
    if (path.endsWith("/diff"))
      return route.fulfill({
        json: {
          files: [
            {
              id: 0,
              old_path: null,
              new_path: "loader.py",
              change: "added",
              old_mode: "000000",
              new_mode: "100644",
            },
          ],
          total_files: 1,
          next_offset: null,
          base_commit: run.base_commit,
          result_commit: run.result_commit,
        },
      });
    if (path.endsWith("/diff/0"))
      return route.fulfill({
        json: {
          file: {
            id: 0,
            old_path: null,
            new_path: "loader.py",
            change: "added",
            old_mode: "000000",
            new_mode: "100644",
          },
          text: "diff --git a/loader.py b/loader.py\nnew file mode 100644\n--- /dev/null\n+++ b/loader.py\n@@ -0,0 +1 @@\n+print('implemented code')\n",
          binary: false,
          omitted_reason: null,
          base_commit: run.base_commit,
          result_commit: run.result_commit,
        },
      });
    if (path.endsWith("/tasks/one/results"))
      return route.fulfill({
        json: {
          items: [version()],
          current_id: run.id,
          current_run_id: run.id,
          next_before: null,
        },
      });
    if (path.endsWith("/cancel")) {
      expect(route.request().postDataJSON().expected_revision).toBe(
        run.revision,
      );
      cancellations++;
      run = { ...run, status: "cancelled", revision: run.revision + 1 };
      return route.fulfill({ json: version() });
    }
    if (path.includes("/results/")) return route.fulfill({ json: version() });
    if (path.endsWith("/integrations/check-one"))
      return route.fulfill({ json: { id: "check-one", checks: [] } });
    if (path.includes("/runs/")) return route.fulfill({ json: run });
    return route.fallback();
  });
  for (const [status, action] of [
    ["preparing", "Stop checks"],
    ["delivering", "Cancel delivery"],
  ]) {
    run = { ...run, status };
    await page.goto("/projects/run-ui/tasks/RUN-1/result/attempt-one");
    const footer = page.getByRole("group", {
      name: "Task actions",
      exact: true,
    });
    await expect(
      footer.getByRole("button", { name: "Archive", exact: true }),
    ).toBeVisible();
    await footer.getByRole("button", { name: action, exact: true }).click();
    await expect(
      page.getByText("Code and check output were kept.", { exact: false }),
    ).toBeVisible();
  }
  expect(cancellations).toBe(2);
  run = { ...run, status: "in_review", revision: 7 };
  await page.goto("/projects/run-ui/tasks/RUN-1/result/attempt-one");
  await page.reload();
  const runs = page.getByRole("region", {
    name: "Proposed result",
    exact: true,
  });
  await expect(runs).toContainText("Implemented the loader");
  await expect(
    page.getByRole("button", { name: "Save changes", exact: true }),
  ).toBeHidden();
  await runs.getByText("Code changes", { exact: true }).click();
  await expect(runs.locator(".diff-code-insert")).toContainText(
    "implemented code",
  );
  await page
    .getByRole("button", { name: "Request changes", exact: true })
    .click();
  await page
    .getByRole("textbox", { name: "Feedback for this result", exact: true })
    .fill("Include the source path in errors.");
  await page.getByRole("button", { name: "Send feedback" }).click();
  await expect(page.getByRole("alert")).toContainText("Review changed");
  await expect(
    page.getByRole("textbox", {
      name: "Feedback for this result",
      exact: true,
    }),
  ).toHaveValue("Include the source path in errors.");
  await page.getByRole("button", { name: "Send feedback" }).click();
  await expect(
    runs.getByRole("heading", { name: "Review request", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("textbox", {
      name: "Feedback for this result",
      exact: true,
    }),
  ).toHaveCount(0);
  await expect(page).toHaveURL(/tasks\/RUN-1\/result\/attempt-one$/);
});

test("shared overlays preserve the workspace, related return paths and mobile creation", async ({
  page,
  request,
}) => {
  const id = "edit"; // A project ID must not be mistaken for an editor route.
  await request.post("/api/projects/initialize", {
    data: { path: existingDirectory(join(state, id)), task_prefix: "UNI" },
  });
  await request.post(`/api/projects/${id}/milestones`, {
    data: { id: "group", title: "First delivery" },
  });
  await request.post(`/api/projects/${id}/tasks`, {
    data: { id: "one", title: "A focused task", milestone_id: "group" },
  });
  await page.goto(`/projects/${id}`);
  const boot = await page.evaluate(() => performance.timeOrigin);
  const board = page.locator(".board");
  await expect(board).toBeVisible();

  await expect(
    page.getByRole("navigation", { name: "Project views", exact: true }),
  ).toContainText("Needs you");
  await expect(page.locator(".up_next .queue-controls")).toContainText(
    "Run queue",
  );
  await expect(page.locator(".up_next .queue-controls")).toContainText(
    "0 active",
  );
  await expect(page.getByText("Local workspace · Preview")).toHaveCount(0);
  await page.getByLabel("Filter by milestone").selectOption("group");
  const card = page.locator(".task-card-link");
  await card.click();
  const dialog = page.getByRole("dialog", {
    name: "Task details",
    exact: true,
  });
  await expect(dialog).toBeVisible();

  await page.keyboard.press("Tab");
  expect(
    await dialog.evaluate((el) => el.contains(document.activeElement)),
  ).toBe(true);
  await page.keyboard.press("Escape");
  if (await dialog.count()) await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(card).toBeFocused();
  await expect(page.getByLabel("Filter by milestone")).toHaveValue("group");
  await page.getByRole("button", { name: /^Milestones(?: \d+)?$/ }).click();
  const row = page.locator(".milestone-list .collection-row");
  await row.click();
  await expect(page.getByRole("tab")).toHaveCount(0);
  await page.getByRole("button", { name: "View tasks", exact: true }).click();
  await expect(page.getByLabel("Filter by milestone")).toHaveValue("group");
  await card.click();
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Close editor" }).click();
  await expect(page).toHaveURL(`/projects/${id}`);
  await page
    .getByRole("button", { name: "Project details", exact: true })
    .click();
  await expect(
    page.getByRole("dialog", { name: "Project details", exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("dialog", { name: "Project details", exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(`/projects/${id}`);
  await page.getByRole("button", { name: /^Milestones(?: \d+)?$/ }).click();
  await expect(page.locator(".milestone-list")).toBeVisible();
  await page
    .getByRole("button", { name: "New milestone", exact: true })
    .click();
  await expect(
    page.getByRole("dialog", { name: "New milestone", exact: true }),
  ).toBeVisible();
  await page.getByLabel("Title", { exact: true }).fill("Draft milestone");
  // Releasing/repeating Escape cannot dismiss the confirmation opened by that key.
  await page.keyboard.down("Escape");
  const discard = page.getByRole("alertdialog", {
    name: "Discard your unsaved edits?",
  });
  await expect(discard).toBeVisible();
  await page.keyboard.up("Escape");
  await page.keyboard.press("Escape");
  await expect(discard).toBeVisible();
  await expect(
    discard.getByRole("button", { name: "Keep editing" }),
  ).toBeFocused();
  await discard.getByRole("button", { name: "Keep editing" }).click();
  await expect(page.getByLabel("Title", { exact: true })).toBeFocused();
  await expect(page.getByLabel("Title", { exact: true })).toHaveValue(
    "Draft milestone",
  );
  await page.keyboard.press("Escape");
  await page
    .getByRole("button", { name: "Discard changes", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Add project", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Decisions", exact: true }).click();
  await page.getByRole("button", { name: "New decision", exact: true }).click();
  await page
    .getByLabel("Decision and rationale")
    .fill("Keep the first delivery local.");
  await page
    .getByRole("button", { name: "Save decision", exact: true })
    .click();
  await expect(
    page.getByRole("dialog", { name: "Decision", exact: true }),
  ).toContainText("Keep the first delivery local.");
  await page
    .getByRole("button", { name: "Close decision", exact: true })
    .click();
  await expect(page).toHaveURL(`/projects/${id}/decisions`);
  await expect(
    page.locator("[data-slot=dialog-content][data-state=open]"),
  ).toHaveCount(0);
  await page.getByRole("button", { name: /^Board / }).click();
  await page.getByRole("button", { name: "New task", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  const create = page.getByRole("dialog", { name: "New task", exact: true });

  await page.getByLabel("Title", { exact: true }).fill("A mobile draft");

  await page.keyboard.press("Escape");
  await page
    .getByRole("button", { name: "Discard changes", exact: true })
    .click();
  await expect(create).toHaveCount(0);
  expect(await page.evaluate(() => document.body.style.overflow)).not.toBe(
    "hidden",
  );
  // The earlier explicit reload is the only document navigation in this journey.
  expect(await page.evaluate(() => performance.timeOrigin)).toBeGreaterThan(
    boot,
  );
});

test("review journey preserves feedback, navigates complete files and reviews a successor from Needs you", async ({
  page,
  request,
}) => {
  const project_id = "review-flow";
  const call = await connectMcp(request);
  await call("initialize_project", {
    project_id,
    path: existingDirectory(join(state, project_id)),
    task_prefix: "RFL",
  });
  await call("create_task", {
    project_id,
    task: { id: "one", title: "Validate CSV records" },
  });
  await call("create_task", {
    project_id,
    task: { id: "two", title: "Document import options" },
  });
  await call("ask_question", {
    project_id,
    question: {
      id: "scope",
      task_id: "RFL-2",
      question: "Which import options should we document?",
      context: "Choose the first release scope.",
      recommendation: "CSV only.",
      blocking_scope: "Documentation scope",
    },
  });
  const baseRun = {
    project_id,
    task_id: "one",
    task_key: "RFL-1",
    revision: 7,
    model: "fixture-model",
    effort: "low",
    created_at: "2026-01-01T10:00:00Z",
    started_at: "2026-01-01T10:00:00Z",
    ended_at: "2026-01-01T10:01:00Z",
    base_commit: "a".repeat(40),
    result_commit: "b".repeat(40),
    predecessor_id: null as string | null,
    result: {
      summary: "Validate every CSV row before importing.",
      checks: "12 deterministic checks passed.",
      limitations: "Large CSVs are not streamed yet.",
    },
    feedback: "",
    problem: null as string | null,
    code_available: false,
    usage: { total_tokens: 100, cached_input_tokens: 50, complete: true },
  };
  let first = { ...baseRun, id: "first", status: "in_review" };
  const older = {
    ...baseRun,
    id: "older",
    status: "changes_requested",
    feedback: "Validate the input rows.",
    result_commit: "c".repeat(40),
  };
  const failure = {
    ...baseRun,
    id: "failed",
    task_id: "two",
    task_key: "RFL-2",
    status: "failed",
    result: null,
    result_commit: null,
    problem: "The check command exited before a result was captured.",
  };
  let successor: typeof first | null = null;
  let failMoreFiles = true;
  let failFilePreview = false;
  const files = [
    {
      id: 0,
      old_path: "loader.py",
      new_path: "loader.py",
      change: "modified",
      old_mode: "100644",
      new_mode: "100644",
    },
    {
      id: 1,
      old_path: null,
      new_path: "tests/test_loader.py",
      change: "added",
      old_mode: "000000",
      new_mode: "100644",
    },
    {
      id: 2,
      old_path: null,
      new_path: "fixture.bin",
      change: "added",
      old_mode: "000000",
      new_mode: "100644",
    },
  ];
  const version = (run: typeof first | typeof failure) => ({
    id: run.id,
    project_id: run.project_id,
    task_id: run.task_id,
    task_key: run.task_key,
    version: run.id === "older" ? 1 : run.id === "first" ? 2 : 3,
    revision: run.revision,
    run_id: run.id,
    completion: "code",
    source_commit: run.result_commit,
    candidate_commit: run.result_commit,
    report: run.result,
    status:
      run.status === "in_review"
        ? "ready"
        : run.status === "accepted"
          ? "delivered"
          : run.status,
    created_at: run.created_at,
    target_branch: "integration",
    integration_id: run.id,
    feedback: run.status === "changes_requested" ? run.feedback : "",
    problem: run.problem,
    next_action:
      run.status === "changes_requested"
        ? {
            owner: "worker_queue",
            action: "wait",
            label: "Waiting for capacity",
            reason:
              "The service will start the requested follow-up when capacity is available.",
          }
        : null,
  });
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route(
    `**/api/projects/${project_id}/view/attention**`,
    async (route) => {
      const response = await route.fetch();
      const data = await response.json();
      const column = new URL(route.request().url()).searchParams.get("column");
      const runs =
        column === "action"
          ? [
              failure,
              ...(successor
                ? successor.status === "in_review"
                  ? [successor]
                  : []
                : [first]),
            ]
          : column === "history"
            ? [
                older,
                ...(successor ? [first] : []),
                ...(successor?.status === "accepted" ? [successor] : []),
              ]
            : [];
      const items = [
        ...data.items,
        ...runs.map((run) => ({
          id: run.id,
          kind: run.status === "failed" ? "intervention" : "result",
          task_key: run.task_key,
          title:
            run.task_id === "two"
              ? "Document import options"
              : "Validate CSV records",
          status: run.status === "in_review" ? "ready" : run.status,
          updated_at: run.created_at,
          code_available: run.code_available,
        })),
      ];
      await route.fulfill({
        json: { items, total: items.length, next_offset: null },
      });
    },
  );
  await page.route(`**/api/projects/${project_id}/**`, async (route) => {
    const url = new URL(route.request().url());
    const segments = url.pathname.split("/");
    const id = segments[5];
    if (url.pathname.endsWith("/thread"))
      return route.fulfill({
        json: {
          items: [
            ...(successor ? [resultMessage(version(successor))] : []),
            resultMessage(version(first)),
            resultMessage(version(older)),
          ],
          next_cursor: null,
        },
      });
    if (url.pathname.includes("/thread/result")) {
      const key = decodeURIComponent(segments.at(-1)!).split(":")[1];
      return route.fulfill({
        json: resultMessage(
          version(
            key === "older" ? older : key === "successor" ? successor! : first,
          ),
        ),
      });
    }
    if (url.pathname.endsWith("/input-eligibility"))
      return route.fulfill({
        json: {
          enabled: true,
          reason: "idle",
          task_revision: 1,
          agreement_revision: 1,
          result_id: (successor ?? first).id,
          result_revision: (successor ?? first).revision,
          question_id: null,
          question_revision: null,
          run_id: null,
          pending_reply_id: null,
        },
      });

    const run =
      id === "successor"
        ? successor!
        : id === "older"
          ? older
          : id === "failed"
            ? failure
            : first;
    if (url.pathname.endsWith("/tasks/one/results"))
      return route.fulfill({
        json: {
          items: [
            ...(successor ? [version(successor)] : []),
            version(first),
            version(older),
          ],
          current_id: (successor ?? first).id,
          current_run_id: (successor ?? first).id,
          next_before: null,
        },
      });
    if (url.pathname.endsWith("/review") || url.pathname.endsWith("/replies")) {
      const body = route.request().postDataJSON();
      if (body.action === "changes") {
        expect(body.binding.result_id).toBe(first.id);
        expect(body.binding.result_revision).toBe(first.revision);
        first = {
          ...first,
          revision: first.revision + 1,
          status: "changes_requested",
          feedback: body.body,
        };
        successor = {
          ...baseRun,
          id: "successor",
          status: "in_review",
          predecessor_id: first.id,
          base_commit: first.result_commit,
          result_commit: "d".repeat(40),
          feedback: body.body,
        };
        return route.fulfill({ json: version(first) });
      }
      successor = { ...successor!, status: "accepted", revision: 8 };
      return route.fulfill({ json: version(successor) });
    }
    if (url.pathname.endsWith("/diff")) {
      if (url.searchParams.has("offset") && failMoreFiles) {
        failMoreFiles = false;
        failFilePreview = true;
        return route.fulfill({
          status: 503,
          json: { error: { message: "More files temporarily unavailable" } },
        });
      }
      return route.fulfill({
        json: {
          files: url.searchParams.has("offset")
            ? files.slice(2)
            : files.slice(0, 2),
          total_files: 3,
          next_offset: url.searchParams.has("offset") ? null : 2,
          base_commit: run.base_commit,
          result_commit: run.result_commit,
        },
      });
    }
    if (url.pathname.includes("/diff/")) {
      const file = files[Number(segments.at(-1))];
      if (file.id === 1 && failFilePreview) {
        failFilePreview = false;
        return route.fulfill({
          status: 503,
          json: { error: { message: "File preview temporarily unavailable" } },
        });
      }
      const text =
        file.id === 0
          ? "diff --git a/loader.py b/loader.py\n--- a/loader.py\n+++ b/loader.py\n@@ -1 +1,160 @@\n-value = 1\n" +
            Array.from({ length: 160 }, (_, i) => `+value_${i} = ${i}\n`).join(
              "",
            )
          : "diff --git a/tests/test_loader.py b/tests/test_loader.py\nnew file mode 100644\n--- /dev/null\n+++ b/tests/test_loader.py\n@@ -0,0 +1 @@\n+assert validate('row')\n";
      return route.fulfill({
        json: {
          file,
          text: file.id === 2 ? null : text,
          binary: file.id === 2,
          omitted_reason:
            file.id === 2 ? "Binary file. No text preview is available." : null,
          base_commit: run.base_commit,
          result_commit: run.result_commit,
        },
      });
    }
    if (url.pathname.endsWith("/location"))
      return route.fulfill({
        json: {
          workspace: "/fixture/worktree",
          diff_command: "git diff BASE RESULT",
          try_command: "Run the reported checks",
        },
      });
    if (url.pathname.endsWith("/runs"))
      return route.fulfill({
        json: {
          items: url.searchParams.has("attention")
            ? [
                ...(successor?.status === "in_review"
                  ? [successor]
                  : first.status === "in_review"
                    ? [first]
                    : []),
                failure,
              ]
            : [...(successor ? [successor] : []), first, older],
          next_before: null,
        },
      });
    if (url.pathname.includes("/integrations/"))
      return route.fulfill({ json: { id: run.id, checks: [] } });
    if (url.pathname.includes("/results/"))
      return route.fulfill({ json: version(run) });
    if (url.pathname.includes("/runs/")) return route.fulfill({ json: run });
    return route.fallback();
  });
  // Result explanations use the same observed state even when a separate settings read fails.
  const workerSettingsPath = `**/api/projects/${project_id}/workers`;
  await page.route(workerSettingsPath, (route) => route.abort());
  first = { ...first, status: "changes_requested" };
  await page.goto(`/projects/${project_id}/tasks/RFL-1/changes/first`);
  await expect(
    page.getByRole("region", { name: "Proposed result", exact: true }).first(),
  ).toContainText(
    "The service will start the requested follow-up when capacity is available.",
  );
  await expect(page.locator("body")).not.toContainText(
    "The worker queue is paused",
  );
  await page.unroute(workerSettingsPath);
  first = { ...first, status: "in_review" };
  await page.goto(`/projects/${project_id}/inbox`);
  const boot = await page.evaluate(() => performance.timeOrigin);
  await expect(
    page.getByRole("link", {
      name: /Changes RFL-1 Validate CSV records Review changes/,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", {
      name: /Intervention RFL-2 Document import options/,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: /Question RFL-2 Which import options/ }),
  ).toBeVisible();

  await page
    .getByRole("link", {
      name: /Changes RFL-1 Validate CSV records Review changes/,
    })
    .click();
  await expect(page).toHaveURL(/changes\/first$/);
  const runs = page
    .getByRole("region", { name: "Proposed result", exact: true })
    .filter({ has: page.getByText("Ready for review", { exact: true }) });
  const checks = runs.getByRole("region", { name: "Checks", exact: true });
  await checks.getByText("Worker’s test report", { exact: true }).click();
  await expect(checks).toContainText("12 deterministic checks passed.");
  await checks
    .getByRole("button", { name: "About these checks" })
    .last()
    .scrollIntoViewIfNeeded();
  await checks
    .getByRole("button", { name: "About these checks" })
    .last()
    .focus();
  await expect(page.getByRole("tooltip")).toContainText(
    "worker’s account of testing in its own checkout",
  );
  await page.keyboard.press("Escape");
  await expect(checks).toBeVisible();
  await expect(page.getByText("Ready for review", { exact: true })).toHaveCount(
    1,
  );
  await expect(
    runs.getByRole("button", { name: "Withdraw review", exact: true }),
  ).toHaveCount(0);
  await runs.getByText("Code changes", { exact: true }).click();
  await expect(runs.locator(".diff-code-insert")).toHaveCount(160);
  await expect(runs.locator(".diff-gutter").first()).toBeVisible();
  await expect(runs.locator(".token.number").first()).toBeVisible();
  await runs.getByRole("button", { name: "Split", exact: true }).click();
  await expect(runs.locator(".diff-split")).toBeVisible();

  const dialog = page.getByRole("dialog", {
    name: "Task details",
    exact: true,
  });

  await runs.locator(".diff-scroll").scrollIntoViewIfNeeded();

  await runs.getByRole("button", { name: "More files", exact: true }).click();
  await expect(runs).toContainText("More files temporarily unavailable");
  await runs
    .getByRole("button", { name: "Added tests/test_loader.py", exact: true })
    .click();
  await expect(runs).toContainText("File preview temporarily unavailable");
  await runs.getByRole("button", { name: "Retry file", exact: true }).click();
  await expect(runs.locator(".diff-code-insert")).toContainText(
    "assert validate",
  );
  await expect(runs).not.toContainText("More files temporarily unavailable");
  await runs.getByRole("button", { name: "More files", exact: true }).click();
  await runs
    .getByRole("button", { name: "Added fixture.bin", exact: true })
    .click();
  await expect(runs).toContainText("No text preview is available.");
  await runs
    .getByRole("button", { name: "Added tests/test_loader.py", exact: true })
    .click();
  await expect(runs.locator(".diff-code-insert")).toContainText(
    "assert validate",
  );
  await page
    .getByRole("button", { name: "Request changes", exact: true })
    .click();
  await page
    .getByLabel("Feedback for this result")
    .fill("Include the source path in validation errors.");
  first = { ...first, revision: first.revision + 1 };
  await request.post(`/api/projects/${project_id}/activity`, {
    data: {
      id: "refresh-review",
      task_id: "one",
      kind: "note",
      body: "Refresh the review fixture.",
      author: "fixture",
    },
  });
  await expect(
    page.getByRole("button", { name: "Send feedback", exact: true }),
  ).toBeDisabled();
  await expect(page.getByRole("status")).toContainText("The context changed");
  await page
    .getByRole("button", { name: "Use current context", exact: true })
    .click();
  await page
    .locator('[data-message-id="result:older"]')
    .getByRole("button", { name: "Inspect earlier result", exact: true })
    .click();
  await expect(page.locator('[data-message-id="result:older"]')).toContainText(
    "Validate the input rows.",
  );
  await expect(page.getByLabel("Feedback for this result")).toHaveValue(
    "Include the source path in validation errors.",
  );
  await expect(
    runs.getByRole("button", {
      name: "Added tests/test_loader.py",
      exact: true,
    }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByLabel("Feedback for this result")).toHaveValue(
    "Include the source path in validation errors.",
  );
  await page
    .getByRole("button", { name: "Send feedback", exact: true })
    .click();
  await expect(page.locator('[data-message-id="result:first"]')).toContainText(
    "Review request",
  );
  await page.reload();
  await runs
    .getByText("Requested in the previous review", { exact: true })
    .click();
  await expect(
    runs.getByText("This revision responds to the request below."),
  ).toBeVisible();
  await expect(
    runs.getByText("Include the source path in validation errors.", {
      exact: true,
    }),
  ).toBeVisible();
  await runs.getByText("Code changes", { exact: true }).click();
  await runs.getByRole("button", { name: "Unified", exact: true }).click();

  await page.setViewportSize({ width: 390, height: 844 });

  await page
    .getByRole("button", { name: "Approve and integrate", exact: true })
    .click();
  await expect(
    page.locator('[data-message-id="result:successor"]'),
  ).toContainText("Task complete.");
  await dialog
    .getByRole("button", { name: "Close editor", exact: true })
    .click();
  await expect(page).toHaveURL(`/projects/${project_id}/inbox`);
  await expect(
    page.getByRole("region", { name: "Needs your action", exact: true }),
  ).toBeVisible();
  expect(await page.evaluate(() => performance.timeOrigin)).not.toBe(boot); // Only the explicit refresh navigated the document.
  expect(errors).toEqual([]);
});

test("validated result approval delivers real Git code with the worker queue paused", async ({
  page,
  request,
}) => {
  const seed = JSON.parse(
    execFileSync(
      join(checkout, ".venv/bin/python"),
      [
        join(checkout, "scripts/create-integration-demo.py"),
        join(state, "delivery-fixture"),
        "--state",
        state,
      ],
      { encoding: "utf8" },
    ),
  );
  const base = `/api/projects/${seed.project}`;
  const mcp = await connectMcp(request);
  const read = await mcp("get_result", {
    project_id: seed.project,
    result_id: seed.result,
  });
  expect(read.status).toBe("ready");
  expect(
    cli(["task", "results", "show", seed.result, "--project", seed.project])
      .candidate_commit,
  ).toBe(seed.candidate);
  await page.goto(`/projects/${seed.project}/inbox`);
  await page.getByRole("link", { name: /INT-1.*Review changes/ }).click();
  await expect(page).toHaveURL(
    new RegExp(`/conversation/result:${seed.result}$`),
  );
  const result = page.getByRole("region", { name: "Proposed result" });
  await expect(result).toContainText("project checks passed");
  await expect(result).toContainText("Passed");
  await expect(result).toContainText("Destination: integration");
  await expect(result.locator(".diff-code-insert")).toHaveCount(0);
  // Offline fixture only: select its fake model without invoking catalog discovery or inference.
  execFileSync(join(checkout, ".venv/bin/python"), [
    "-c",
    "import sys; from pathlib import Path; from flowfield.application import Workspace; from flowfield.execution import Execution; from flowfield.execution_models import SettingsEdit; e=Execution(Workspace(Path(sys.argv[1]))); e.configure(sys.argv[2],SettingsEdit(expected_revision=e.settings(sys.argv[2]).revision,model='offline-fixture',effort='none'))",
    state,
    seed.project,
  ]);
  await expect(
    page.getByLabel("Message the worker", { exact: true }),
  ).toHaveCount(0);
  await askWorker(page);
  const replyInput = page.getByLabel("Message the worker", { exact: true });
  await replyInput.fill("Why is this validation sufficient?");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(replyInput).toHaveCount(0);
  await expect(page.getByRole("list", { name: "Task feed" })).toContainText(
    "Why is this validation sufficient?",
  );
  await expect(
    page.getByRole("button", { name: "Approve and integrate", exact: true }),
  ).toBeDisabled();
  await page.reload();
  await expect(replyInput).toHaveCount(0);
  await page
    .getByRole("button", { name: "Cancel pending message", exact: true })
    .click();
  await askWorker(page);
  await expect(replyInput).toBeEnabled();
  expect((await (await request.get(base + "/workers")).json()).enabled).toBe(
    false,
  );

  await page
    .getByRole("button", { name: "Approve and integrate", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Retry integration", exact: true }),
  ).toBeVisible();
  expect((await (await request.get(`${base}/tasks/INT-1`)).json()).status).toBe(
    "in_review",
  );
  const preserved = readFileSync(join(seed.repository, "README.md"), "utf8");
  expect(preserved).toContain("Unsaved human planning notes");
  writeFileSync(join(state, "preserved-human-readme.txt"), preserved);
  execFileSync("git", [
    "-C",
    seed.repository,
    "restore",
    "--worktree",
    "README.md",
  ]);
  await page
    .getByRole("button", { name: "Retry integration", exact: true })
    .click();
  await expect(result).toContainText("Delivered to integration.");
  const task = await (await request.get(`${base}/tasks/INT-1`)).json();
  expect(task.status).toBe("done");
  const replay = await mcp("review_result", {
    project_id: seed.project,
    result_id: seed.result,
    review: {
      expected_revision: seed.result_revision,
      candidate_commit: seed.candidate,
      action: "approve",
      author: "fixture",
    },
  });
  expect(replay.status).toBe("delivered");
  const git = (...args: string[]) =>
    execFileSync("git", ["-C", seed.repository, ...args], {
      encoding: "utf8",
    }).trim();
  expect(git("rev-parse", "integration")).toBe(seed.candidate);
  expect(git("rev-parse", "HEAD")).toBe(seed.candidate);
  expect(existsSync(join(seed.repository, "catalog.py"))).toBe(true);
  expect(git("status", "--porcelain")).toBe("");
  await page.reload();
  await expect(result).toContainText("Task complete.");
  await expect(
    page.getByRole("button", { name: "Approve and integrate" }),
  ).toHaveCount(0);
  const timeline = page.locator(".conversation-messages");
  const history = timeline.locator('[data-kind="attempt"]');
  await expect(history).toHaveCount(1);
  await askWorker(page);
  await page
    .getByLabel("Message the worker", { exact: true })
    .fill("Keep this question draft while inspecting evidence.");
  await expect(
    history.getByText("Execution details", { exact: true }),
  ).toHaveCount(0);
  await result.getByText(/^Flowfield: .*project checks passed$/).click();
  await expect(
    result.getByText("Project commands", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByLabel("Message the worker", { exact: true }),
  ).toHaveValue("Keep this question draft while inspecting evidence.");
  await page.getByLabel("Message the worker", { exact: true }).fill("");
  await expect(
    history.getByRole("heading", { name: "Worker report", exact: true }),
  ).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  // Saved exact-execution URLs still reveal only the requested historical evidence.
  await page.goto(
    `/projects/${seed.project}/tasks/INT-1/history/delivery:${seed.result}`,
  );
  await expect(
    history.getByRole("heading", { name: "Review 1 delivery", exact: true }),
  ).toBeVisible();
  await expect(history).toContainText("Approved by human");
  const advanced = git(
    "-c",
    "user.name=Fixture",
    "-c",
    "user.email=fixture@example.invalid",
    "commit-tree",
    `${seed.candidate}^{tree}`,
    "-p",
    seed.candidate,
    "-m",
    "External target advance",
  );
  git("update-ref", "refs/heads/integration", advanced);
  await history
    .getByRole("link", { name: "Review these changes" })
    .first()
    .click();
  await expect(
    page.getByRole("button", { name: "Check destination" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Check destination" }).click();
  await expect(
    result.getByRole("region", { name: "Current branch checks" }),
  ).toContainText("Passed");
  await expect(
    page.getByRole("button", { name: "Check destination" }),
  ).toHaveCount(0);
  expect(git("rev-parse", "integration")).toBe(advanced);
  await expect(result).toContainText("Task complete.");
  await result.getByRole("link", { name: "Open branch check details" }).click();
  await expect(
    history.getByRole("heading", { name: "Review 1 branch checks" }),
  ).toBeVisible();
});

test("closing entity overlays returns through history without duplicate collections", async ({
  page,
  request,
}) => {
  const id = "history-roundtrip";
  await request.post("/api/projects/initialize", {
    data: { path: existingDirectory(join(state, id)), task_prefix: "HIS" },
  });
  await request.post(`/api/projects/${id}/tasks`, {
    data: { id: "one", title: "History task" },
  });
  const call = await connectMcp(request);
  await call("ask_question", {
    project_id: id,
    question: {
      id: "scope",
      task_id: "HIS-1",
      question: "Which scope?",
      context: "Choose the scope",
      recommendation: "Small",
    },
  });
  const board = `/projects/${id}`;
  const inbox = `${board}/inbox`;
  await page.goto(board);
  await page.getByRole("button", { name: /^Needs you(?: \d+)?$/ }).click();
  await page.locator(".attention-card").first().click();
  await page.getByRole("button", { name: "Close editor", exact: true }).click();
  await expect(page).toHaveURL(inbox);
  await page.goBack();
  await expect(page).toHaveURL(board);
  await page.goForward();
  await expect(page).toHaveURL(inbox);
  await page.goForward();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.reload();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(inbox);
  await page.goBack();
  await expect(page).toHaveURL(board);
  await page.getByRole("link", { name: /HIS-1/ }).first().click();
  await page.getByRole("button", { name: "Close editor", exact: true }).click();
  await expect(page).toHaveURL(board);
  // Direct links have no known in-app origin, so Close stays in the project.
  await page.goto(`${board}/tasks/HIS-1`);
  await page.getByRole("button", { name: "Close editor", exact: true }).click();
  await expect(page).toHaveURL(board);
});

test("worker models load automatically and retry without replacing setting drafts", async ({
  page,
  request,
}) => {
  const project = "model-discovery";
  await request.post("/api/projects/initialize", {
    data: { path: existingDirectory(join(state, project)), task_prefix: "MDL" },
  });
  let calls = 0;
  await page.route("**/api/worker-models", async (route) => {
    calls++;
    if (calls === 1)
      return route.fulfill({
        status: 503,
        json: { error: { message: "Catalog temporarily unavailable" } },
      });
    return route.fulfill({
      json: [
        { id: "model-one", name: "Model one", efforts: ["low", "high"] },
        { id: "model-two", name: "Model two", efforts: ["medium"] },
      ],
    });
  });
  await page.goto(`/projects/${project}/edit`);
  await page.getByRole("tab", { name: "Workers", exact: true }).click();
  const settings = page.getByRole("region", { name: "Worker settings" });
  await expect(settings).toContainText("Catalog temporarily unavailable");
  await expect(
    settings.getByRole("button", { name: "Refresh models" }),
  ).toHaveCount(0);
  await settings.getByLabel("Maximum parallel workers").fill("3");
  await settings.getByRole("button", { name: "Reload models" }).click();
  const model = settings.getByLabel("Model", { exact: true });
  const effort = settings.getByLabel("Reasoning effort");
  await expect(model.getByRole("option", { name: "Model one" })).toBeAttached();
  await expect(model).toHaveValue("");
  await expect(settings.getByLabel("Maximum parallel workers")).toHaveValue(
    "3",
  );
  await model.selectOption("model-one");
  await effort.selectOption("high");
  await model.selectOption("model-two");
  await expect(effort).toHaveValue("");
  await expect(
    effort.getByRole("option", { name: "high", exact: true }),
  ).toHaveCount(0);
  await effort.selectOption("medium");
  expect(calls).toBe(2);
});

test("integration settings create an explicit local target without changing the human checkout", async ({
  page,
  request,
}) => {
  const project = "integration-settings";
  const repo = join(state, project);
  await request.post("/api/projects/initialize", {
    data: { path: existingDirectory(repo), task_prefix: "IGS" },
  });
  const git = (...args: string[]) =>
    execFileSync(
      "git",
      ["-C", repo, "-c", "core.hooksPath=/dev/null", ...args],
      { encoding: "utf8" },
    ).trim();
  git("init", "-b", "main");
  git("add", ".");
  git(
    "-c",
    "user.name=Fixture",
    "-c",
    "user.email=fixture@example.invalid",
    "commit",
    "-m",
    "base",
  );
  const head = git("rev-parse", "HEAD");
  await page.goto(`/projects/${project}/edit`);
  await page.getByRole("tab", { name: "Integration", exact: true }).click();
  const settings = page.getByRole("region", {
    name: "Integration settings",
    exact: true,
  });
  await settings
    .getByLabel("Destination branch", { exact: true })
    .fill("integration");
  await expect(
    settings.getByRole("checkbox", { name: "Create this branch" }),
  ).toHaveCount(0);
  await expect(settings.getByLabel("Executable paths")).toBeVisible();
  await expect(settings.getByLabel("Seconds per setup command")).toBeVisible();
  await settings
    .getByLabel("Validation commands", { exact: true })
    .fill("python -m unittest -v");
  await settings
    .getByLabel("Run command", { exact: true })
    .fill("python app.py");
  let runSaves = 0;
  await page.route(
    `**/api/projects/${project}/inspection/settings`,
    (route) => {
      if (route.request().method() === "PUT" && ++runSaves === 1)
        return route.fulfill({
          status: 503,
          json: { error: { message: "Run command save interrupted" } },
        });
      return route.continue();
    },
  );
  const saved = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/projects/${project}/integration`) &&
      response.request().method() === "PUT",
  );
  await settings
    .getByRole("button", { name: "Save integration settings", exact: true })
    .click();
  expect((await saved).ok()).toBe(true);
  await expect(settings).toContainText("Run command save interrupted");
  await expect(settings.getByLabel("Run command", { exact: true })).toHaveValue(
    "python app.py",
  );
  // Integration was saved; retry only the pending run command without revising delivery settings.
  await settings
    .getByRole("button", { name: "Save integration settings", exact: true })
    .click();
  await expect(
    settings.getByRole("button", {
      name: "Save integration settings",
      exact: true,
    }),
  ).toBeDisabled();
  expect(
    (await (await request.get(`/api/projects/${project}/integration`)).json())
      .revision,
  ).toBe(2);
  expect(
    (
      await (
        await request.get(`/api/projects/${project}/inspection/settings`)
      ).json()
    ).run_command,
  ).toBe("python app.py");
  // Deterministic UI projection only: real sandbox validation is a separate opt-in probe.
  await page.route(
    `**/api/projects/${project}/setup-validation`,
    async (route) => {
      if (route.request().method() !== "POST") return route.continue();
      expect(route.request().postDataJSON().expected_revision).toBe(2);
      await route.fulfill({
        json: {
          project_id: project,
          id: "setup-check",
          settings_revision: 2,
          commit: head,
          created_at: new Date().toISOString(),
          status: "failed",
          stale: false,
          problem: "The required compiler is unavailable in this environment.",
          checkout_problem:
            "Switch the project checkout to 'integration', then retry integration. Local files are unchanged.",
          setup: [
            {
              command: "compiler --version",
              exit_code: 127,
              output: "compiler: not found",
              truncated: false,
            },
          ],
          checks: [],
          workspace: null,
          pid: null,
          commands: [],
        },
      });
    },
  );
  await settings.getByRole("button", { name: "Validate saved setup" }).click();
  await expect(
    settings.getByText(
      "The required compiler is unavailable in this environment.",
    ),
  ).toBeVisible();
  await expect(
    settings.getByText("compiler: not found", { exact: true }),
  ).toBeVisible();
  await expect(
    settings
      .getByRole("alert")
      .filter({ hasText: "Checkout is not ready for delivery" }),
  ).toContainText("Switch the project checkout");
  await settings.getByLabel("Setup commands").fill("install-dependencies");
  await expect(
    settings.getByRole("button", { name: "Validate saved setup" }),
  ).toBeDisabled();
  await settings.getByLabel("Setup commands").fill("");
  expect(git("rev-parse", "refs/heads/integration")).toBe(head);
  expect(git("symbolic-ref", "--short", "HEAD")).toBe("main");
  await page.reload();
  await expect(
    settings.getByLabel("Destination branch", { exact: true }),
  ).toHaveValue("integration");
  await expect(
    settings.getByLabel("Validation commands", { exact: true }),
  ).toHaveValue("python -m unittest -v");
  expect((await request.get(`/api/projects/${project}/workers`)).ok()).toBe(
    true,
  );
  expect(
    (await (await request.get(`/api/projects/${project}/workers`)).json())
      .enabled,
  ).toBe(false);
});

test("entity identity and drafts persist across task and project tabs", async ({
  page,
  request,
}) => {
  const project = "identity-layout";
  await request.post("/api/projects/initialize", {
    data: { path: existingDirectory(join(state, project)), task_prefix: "IDN" },
  });
  await request.post(`/api/projects/${project}/milestones`, {
    data: { id: "first", title: "First milestone" },
  });
  await request.post(`/api/projects/${project}/tasks`, {
    data: { id: "first", title: "First task", milestone_id: "first" },
  });
  await page.goto("/");
  const sidebar = page.locator(".sidebar");
  const logo = sidebar.getByRole("link", { name: "f flowfield", exact: true });
  const projectLink = sidebar.getByRole("link", { name: project, exact: true });
  await expect(projectLink).toBeVisible();
  await expect(
    sidebar.getByRole("button", { name: "Board", exact: true }),
  ).toHaveCount(0);

  await expect(logo).toHaveAttribute("href", "/");

  await projectLink.click();
  await page.getByRole("link", { name: /IDN-1/ }).click();
  const editor = page.getByRole("dialog", {
    name: "Task details",
    exact: true,
  });
  const title = page.getByRole("dialog").getByRole("heading", {
    name: "IDN-1 · First task",
    exact: true,
  });

  await editor
    .getByRole("link", { name: "Permalink: Task defined", exact: true })
    .click();
  await expect(title).toBeVisible();
  await expect(editor.getByRole("tab")).toHaveCount(0);
  await expect(
    editor.getByText("Loading execution…", { exact: true }),
  ).toHaveCount(0);
  await expect(editor.getByLabel("Add prerequisite")).toHaveCount(0);
  await expect(
    editor.getByRole("button", { name: "Edit", exact: true }),
  ).toHaveCount(0);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "New task", exact: true }).click();
  const creation = page.getByRole("region", { name: "New task", exact: true });
  await expect(creation.getByRole("tab")).toHaveCount(0);
  await expect(creation.getByLabel("Title", { exact: true })).toBeVisible();
  await expect(creation.getByLabel("Add prerequisite")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await page
    .getByRole("button", { name: "Project details", exact: true })
    .click();
  await page.getByRole("tab", { name: "Workers", exact: true }).click();
  await expect(page).toHaveURL(
    new RegExp(`/projects/${project}/edit/workers$`),
  );
  await page.getByLabel("Maximum parallel workers").fill("3");
  await page.getByRole("tab", { name: "Integration", exact: true }).click();
  await page
    .getByLabel("Destination branch", { exact: true })
    .fill("draft-target");
  await page.getByRole("tab", { name: "Workers", exact: true }).click();
  await expect(page.getByLabel("Maximum parallel workers")).toHaveValue("3");
  await page.getByRole("tab", { name: "Integration", exact: true }).click();
  await expect(
    page.getByLabel("Destination branch", { exact: true }),
  ).toHaveValue("draft-target");
  // Switch to mobile, then deliberately discard the drafts on refresh.
  await page.setViewportSize({ width: 390, height: 844 });

  page.on("dialog", (dialog) => dialog.accept());
  await page.reload();
  await expect(
    page.getByRole("tab", { name: "Integration", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(
    page.getByLabel("Destination branch", { exact: true }),
  ).toHaveValue("");
});

test("queue errors open shared notifications with a direct settings action", async ({
  page,
  request,
}) => {
  const project = "queue-notices";
  await request.post("/api/projects/initialize", {
    data: { path: existingDirectory(join(state, project)), task_prefix: "NTF" },
  });
  await page.goto(`/projects/${project}`);
  await page.getByRole("button", { name: "Run queue", exact: true }).click();
  const notices = page.getByRole("dialog", {
    name: "Notifications",
    exact: true,
  });
  await expect(notices).toBeVisible();
  await expect(notices).not.toContainText(
    "Messages from this browser session.",
  );
  await expect(notices).not.toContainText(
    "Notify when your input is needed while",
  );
  await expect(
    notices.getByRole("heading", {
      name: "Queue could not start",
      exact: true,
    }),
  ).toBeVisible();
  await expect(notices).toContainText(/model/i);
  await expect(page.locator(".queue-controls [role=alert]")).toHaveCount(0);

  await notices
    .getByRole("alert")
    .filter({ hasText: "Queue could not start" })
    .getByRole("link", { name: "Worker settings", exact: true })
    .click();
  await expect(notices).toHaveCount(0);
  await expect(page).toHaveURL(
    new RegExp(`/projects/${project}/edit/workers$`),
  );
  await expect(
    page.getByRole("tab", { name: "Workers", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: /Notifications/ }).click();
  await expect(
    notices.getByText("Queue could not start", { exact: true }),
  ).toBeVisible();
  await notices
    .getByRole("button", { name: "Clear notifications", exact: true })
    .click();
  await expect(
    notices.getByText("No notifications.", { exact: true }),
  ).toBeVisible();
});

test.describe("relative timestamps", () => {
  test.use({ locale: "en-US", timezoneId: "America/Denver" });

  test("timestamps refresh and expose the exact local time on hover and focus", async ({
    page,
  }) => {
    const project = "relative-times";
    cli([
      "project",
      "init",
      existingDirectory(join(state, project)),
      "--prefix",
      "TIM",
    ]);
    const decision = cli([
      "project",
      "decide",
      "--project",
      project,
      "--body",
      "Keep timestamp evidence accessible.",
    ]);
    const created = Date.parse(decision.created_at);
    await page.clock.install({ time: new Date(created) });
    await page.clock.pauseAt(new Date(created + 10000));
    await page.goto(`/projects/${project}/decisions`);
    const card = page.locator(".decision-cards .collection-row");
    const time = card.locator("time");
    await expect(time).toHaveText("10 seconds ago");
    await expect(time).toHaveAttribute("datetime", decision.created_at);
    const exact = new Intl.DateTimeFormat("en-US", {
      dateStyle: "full",
      timeStyle: "long",
      timeZone: "America/Denver",
    }).format(new Date(created));
    await time.hover();
    await page.clock.runFor(100);
    await expect(page.getByRole("tooltip")).toHaveText(exact);
    await page.mouse.move(0, 0);
    await page.clock.runFor(100);
    await card.focus();
    await expect(page.getByRole("tooltip")).toHaveText(exact);
    // Time advances without a data refresh; the date behind the label stays fixed.
    await page.clock.fastForward(290000);
    await expect(time).toHaveText("5 minutes ago");
    await expect(page.getByRole("tooltip")).toHaveText(exact);
    await card.click();
    const detail = page.getByRole("dialog", { name: "Decision", exact: true });
    const detailTime = detail.locator("time");
    await expect(detailTime).toHaveText("5 minutes ago");
    await detailTime.focus();
    await expect(page.getByRole("tooltip")).toHaveText(exact);
  });
});

test("existing-project adoption previews and preserves coordinator guidance", async ({
  page,
}) => {
  const root = existingDirectory(join(state, "adoption-guidance"));
  execFileSync("git", ["init", "-b", "main", root], { stdio: "ignore" });
  const agents = join(root, "AGENTS.md");
  const original = "# Repository rules\n\nKeep our conventions.\n";
  writeFileSync(agents, original);
  writeFileSync(join(root, "AGENTS.override.md"), "Local override\n");
  await page.goto("/new-project");
  await expect(
    page.getByRole("region", { name: "Project setup instructions" }),
  ).toBeVisible();
  await expect(
    page.getByText("flowfield --port 8766 project init", { exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("Existing project directory")).toHaveCount(0);
  const adopted = cli(["project", "init", root]);
  await page.goto("/projects/" + adopted.id + "/edit/coordinator");
  await expect(
    page.getByRole("heading", { name: "Coordinator setup", exact: true }),
  ).toBeVisible();
  await page.getByText("Preview or copy guidance", { exact: true }).click();
  await expect(page.getByRole("button", { name: "Copy skill" })).toBeVisible();
  expect(readFileSync(agents, "utf8")).toBe(original);
  await page.getByRole("button", { name: "Install project guidance" }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "Project guidance installed" }),
  ).toBeVisible();
  await expect(
    page.getByText(/^Untracked adoption files:.*config.toml.*guidance.json/),
  ).toContainText("Leaving them untracked blocks code delivery");
  expect(readFileSync(agents, "utf8").startsWith(original)).toBe(true);
  expect(
    existsSync(join(root, ".agents/skills/flowfield-coordinator/SKILL.md")),
  ).toBe(true);
  await expect(
    page.getByText("Instruction files and worker baseline", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText("Remove installed guidance", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText(
      "Review additional instruction files: AGENTS.override.md. They may override this guidance.",
      {
        exact: true,
      },
    ),
  ).toBeVisible();
  await expect(
    page.getByText(
      "Start a fresh Codex conversation; open sessions do not reload guidance.",
      { exact: true },
    ),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Install project guidance" }),
  ).toBeDisabled();
  writeFileSync(agents, readFileSync(agents, "utf8") + "\nNew local rule\n");
  cli(["project", "guidance", "remove"], root);
  expect(readFileSync(agents, "utf8")).toBe(original + "\nNew local rule\n");
  expect(
    existsSync(join(root, ".agents/skills/flowfield-coordinator/SKILL.md")),
  ).toBe(false);
  await closeOverlay(page);
  await expect(page).toHaveURL(/\/projects\/adoption-guidance$/);
});

test("conversation capture prepares atomically while task defaults show only useful work", async ({
  page,
  request,
}) => {
  const project_id = "conversation-capture";
  await request.post("/api/projects/initialize", {
    data: {
      path: existingDirectory(join(state, project_id)),
      task_prefix: "CAP",
    },
  });
  await page.goto(`/projects/${project_id}`);
  await expect(
    page.getByRole("link", { name: "Set up your coordinator" }),
  ).toHaveCount(0);
  const call = await connectMcp(request);
  const board = await call("get_board", { project_id });
  const task = await call("create_task", {
    project_id,
    task: {
      title: "Investigate import formats",
      body: "Compare existing formats, evidence and a recommendation; deliver a report without changing repository files.",
      task_type: "investigation",
      preparation: {
        completion: "report",
        expected_decision_sequence: board.decision_sequence,
      },
    },
  });
  expect(task.publication_status).toBe("published");
  expect(task.status).toBe("backlog");
  expect((await call("get_workers", { project_id })).enabled).toBe(false);
  await page.goto(`/projects/${project_id}/tasks/CAP-1`);
  const editor = page.getByRole("dialog", {
    name: "Task details",
    exact: true,
  });
  for (const text of [
    "No prerequisites.",
    "No dependent tasks.",
    "No dependency or input blockers.",
    "DRAFT",
  ])
    await expect(editor.getByText(text, { exact: true })).toHaveCount(0);
  await expect(editor.getByText("Related work", { exact: true })).toHaveCount(
    0,
  );
  await expect(
    editor.getByRole("heading", { name: "Waiting on", exact: true }),
  ).toHaveCount(0);
  const planTask = await call("get_task", { project_id, task_id: task.id });
  await call("update_task_stages", {
    project_id,
    task_id: task.id,
    request: {
      expected_revision: 0,
      agreement_revision: planTask.agreement_revision,
      stages: [
        {
          id: "investigate",
          title: "Investigate",
          outcome: "Compare available formats",
          status: "planned",
        },
        {
          id: "report",
          title: "Report",
          outcome: "Present findings",
          status: "planned",
        },
      ],
      reason: "A short investigation plan",
    },
  });
  const stages = page.getByRole("group", {
    name: "Task stages",
    exact: true,
  });
  await expect(page.locator('[data-message-id="plan:1"]')).toContainText(
    "A short investigation plan",
  );
  await expect(stages.getByRole("link")).toHaveCount(0);
  // The sticky stage is already visible; focus it without scrolling the feed.
  await stages
    .locator(".task-stage")
    .first()
    .evaluate((el) => (el as HTMLElement).focus({ preventScroll: true }));
  await expect(page.getByRole("tooltip")).toContainText(
    "Compare available formats",
  );
  await page
    .getByRole("link", { name: "Permalink: Stages defined", exact: true })
    .click();
  await expect(page).toHaveURL(/conversation\/plan%3A1$/);
  await expect(page.locator('[data-message-id="plan:1"]')).toContainText(
    "A short investigation plan",
  );
  const stageSnapshot = await call("get_task_stages", {
    project_id,
    task_id: task.id,
  });
  await call("update_task_stages", {
    project_id,
    task_id: task.id,
    request: {
      expected_revision: stageSnapshot.revision,
      agreement_revision: planTask.agreement_revision,
      stages: stageSnapshot.stages.map((stage: { id: string }) => ({
        ...stage,
        status: stage.id === "investigate" ? "completed" : "active",
      })),
      reason: "Evidence gathered; writing findings.",
    },
  });
  const changedStages = page.locator('[data-message-id="plan:2"]');
  await expect(changedStages).toContainText("Stage changed");
  await expect(changedStages).toContainText(
    "Investigate: Planned → Completed.",
  );
  await expect(stages.locator('[aria-current="step"]')).toHaveText(
    "Report: Active",
  );
  await expect(changedStages.locator('[aria-current="step"]')).toHaveText(
    "Report: Active",
  );
  await expect(
    page.locator('[data-message-id="plan:1"] [aria-current="step"]'),
  ).toHaveCount(0);
  await expect(page.locator('[data-message-id="plan:1"]')).toContainText(
    "Investigate: Planned",
  );
  await page.reload();
  await expect(stages.locator('[aria-current="step"]')).toHaveText(
    "Report: Active",
  );
  await expect(page.locator('[data-message-id="plan:1"]')).toContainText(
    "Investigate: Planned",
  );
  const current = await call("get_task", { project_id, task_id: task.id });
  const updated = await call("edit_task", {
    project_id,
    task_id: task.id,
    changes: {
      expected_revision: current.revision,
      body: "Compare formats, include uncertainty and recommend one; report only with no file changes.",
      preparation: {
        completion: "report",
        expected_decision_sequence: current.decision_sequence,
      },
    },
  });
  expect(updated.publication_status).toBe("published");
  await expect(
    editor
      .getByLabel("Current task definition")
      .getByText(/Compare formats, include uncertainty/),
  ).toBeVisible();
  const stale = await request.put(
    `/api/projects/${project_id}/tasks/${task.id}`,
    {
      data: {
        expected_revision: current.revision,
        body: "Stale replacement",
        preparation: {
          completion: "report",
          expected_decision_sequence: current.decision_sequence,
        },
      },
    },
  );
  expect(stale.status()).toBe(409);
  const related = await call("create_task", {
    project_id,
    task: { title: "Follow findings", dependencies: [task.id] },
  });
  await expect(editor.getByText("Related work", { exact: true })).toBeVisible();
  await expect(editor.getByText("Needed by:", { exact: false })).toBeHidden();
  await editor.getByText("Related work", { exact: true }).click();
  await expect(editor.getByText("Needed by:", { exact: false })).toBeVisible();
  await expect(
    editor.getByRole("link", { name: related.key, exact: true }),
  ).toBeVisible();
  await expect(
    editor.getByRole("button", { name: "Edit", exact: true }),
  ).toHaveCount(0);
  expect((await call("get_workers", { project_id })).enabled).toBe(false);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await expect(
    editor
      .getByLabel("Current task definition")
      .getByText(/Compare formats, include uncertainty/),
  ).toBeVisible();
});

test("managed answers show durable pause, safe edits and immutable correction input", async ({
  page,
  request,
}) => {
  const fixture = (operation: string) =>
    JSON.parse(
      execFileSync(
        "uv",
        [
          "run",
          "--project",
          checkout,
          "python",
          join(checkout, "tests/input_browser_fixture.py"),
          state,
          operation,
        ],
        { encoding: "utf8" },
      ),
    );
  fixture("create");
  await page.goto("/projects/input-browser/tasks/INP-1");
  const inputArea = page.getByRole("form", { name: "Task input", exact: true });
  await expect(inputArea).toContainText("Answer the worker");
  await expect(
    page.getByRole("button", { name: "Stop worker", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Reply", exact: true }),
  ).toHaveCount(0);
  await page.getByLabel("Your answer", { exact: true }).fill("All records");
  await page.getByRole("button", { name: "Send answer", exact: true }).click();
  await expect(page.getByRole("list", { name: "Task feed" })).toContainText(
    "All records",
  );
  await expect(
    page.getByRole("textbox", { name: "Your answer", exact: true }),
  ).toHaveCount(0);
  await page.goto("/projects/input-browser/inbox/which-records");
  const detail = page.getByRole("dialog", {
    name: "Task details",
    exact: true,
  });
  await expect(
    detail.locator(".task-conversation-header .work-state"),
  ).toHaveText("Paused");
  await expect(
    detail.getByRole("button", { name: "Retract", exact: true }),
  ).toHaveCount(0);
  await detail
    .getByRole("button", { name: "Edit answer", exact: true })
    .click();
  const editedAnswer = detail.getByLabel("Your answer", { exact: true });
  await expect(editedAnswer).toBeFocused();
  await editedAnswer.fill("");
  await expect(editedAnswer).toBeVisible();
  await expect(
    detail.getByRole("button", { name: "Send answer", exact: true }),
  ).toBeDisabled();
  await detail
    .getByRole("button", { name: "Cancel edit", exact: true })
    .click();
  await expect(editedAnswer).toHaveCount(0);
  await detail
    .getByRole("button", { name: "Edit answer", exact: true })
    .click();
  await expect(editedAnswer).toHaveValue("All records");
  await editedAnswer.fill("");
  await editedAnswer.fill("Only favourites");
  await detail
    .getByRole("button", { name: "Send answer", exact: true })
    .click();
  await page.reload();
  await expect(
    detail.getByText("Only favourites", { exact: true }),
  ).toBeVisible();
  await expect(
    detail.locator(".task-conversation-header .work-state"),
  ).toHaveText("Paused");
  const counts = await request.get(
    "/api/projects/input-browser/view/attention?column=action",
  );
  expect((await counts.json()).total).toBe(0);
  fixture("consume");
  await page.reload();
  await expect(
    detail.locator(".task-conversation-header .work-state"),
  ).toHaveText("Preparing worker");
  await expect(
    detail.getByRole("button", { name: "Edit answer", exact: true }),
  ).toHaveCount(0);
  // A running worker closes input. External correction is immutable evidence,
  // and must appear in the same task feed rather than a second question editor.
  await expect(detail.getByRole("textbox")).toHaveCount(0);
  const original = await (
    await request.get("/api/projects/input-browser/questions/which-records")
  ).json();
  const corrected = await request.post(
    "/api/projects/input-browser/questions/which-records/correction",
    {
      data: {
        expected_revision: original.revision,
        answer: "I changed my mind: all records.",
      },
    },
  );
  expect(corrected.ok()).toBe(true);
  await expect(detail.locator('[data-kind="answer"]')).toContainText([
    "All records",
    "Only favourites",
    "I changed my mind: all records.",
  ]);
});

test("inspection preserves exact versions, preview edits and direct approval", async ({
  page,
  request,
}) => {
  // Two result generations and multiple Git copies need a bounded hosted-runner budget.
  test.setTimeout(60000);
  const project = "inspection-browser";
  const fixture = (operation: string) =>
    execFileSync(
      "uv",
      [
        "run",
        "--project",
        checkout,
        "python",
        join(checkout, "tests/inspection_browser_fixture.py"),
        state,
        operation,
      ],
      { encoding: "utf8" },
    );
  fixture("create");
  const path = `/api/projects/${project}`;
  let first: { id: string; revision: number; candidate_commit: string };
  await expect(async () => {
    const versions = await (
      await request.get(`${path}/tasks/try/results`)
    ).json();
    expect(versions.items[0]?.status).toBe("ready");
    first = versions.items[0];
  }).toPass();
  await page.route(
    "**/api/projects/inspection-browser/results/*",
    async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 150));
      await route.continue();
    },
  );
  const atBottom = () =>
    page
      .locator(".entity-overlay-body")
      .evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight < 3);
  for (let i = 0; i < 3; i++) {
    await page.goto(`/projects/${project}/tasks/IPV-1`);
    await expect(
      page.getByRole("region", { name: "Proposed result" }),
    ).toContainText("Ready for review");
    await expect.poll(atBottom).toBe(true);
    await expect(page.getByRole("form", { name: "Task input" })).toContainText(
      "Review Result 1",
    );
    await page
      .getByRole("button", { name: "Close editor", exact: true })
      .click();
  }
  await page.goto(`/projects/${project}/tasks/IPV-1/changes`);
  let selectedPreview = first!.id;
  const preview = () =>
    page
      .locator(`[data-message-id="result:${selectedPreview}"]`)
      .getByRole("region", { name: "Try result", exact: true });
  await expect(
    preview()
      .locator("summary")
      .filter({ hasText: /^Try result$/ }),
  ).toBeVisible();
  expect(
    await (
      await request.get(`${path}/inspection?result_id=${first!.id}`)
    ).json(),
  ).toBeNull();
  await preview()
    .locator("summary")
    .filter({ hasText: /^Try result$/ })
    .click();
  await expect(
    preview().getByText("Run in your terminal", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Inspection", exact: true }),
  ).toHaveCount(0);
  await expect(
    preview().getByText("Inspection details", { exact: true }),
  ).toHaveCount(0);
  await expect(
    preview().getByRole("button", { name: "Copy commands", exact: true }),
  ).toBeHidden();
  await preview().getByText("Terminal commands", { exact: true }).click();
  await expect(
    preview().getByRole("button", { name: "Copy commands", exact: true }),
  ).toBeVisible();
  const copy = await (
    await request.get(`${path}/inspection?result_id=${first!.id}`)
  ).json();
  expect(copy.commit).toBe(first!.candidate_commit);
  expect(copy.command).toMatch(/^\/bin\/sh /);
  await expect(preview().locator("pre")).toHaveText(copy.command);
  expect(
    execFileSync("/bin/sh", ["-c", copy.command], { encoding: "utf8" }),
  ).toContain("First greeting");
  writeFileSync(join(copy.workspace, "app.py"), "print('Preview edit')\n");
  const beforeTesting = await (
    await request.get(`${path}/tasks/try/results`)
  ).json();
  await page
    .getByRole("button", { name: "Record testing", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Approve and integrate", exact: true }),
  ).toBeHidden();
  await page
    .getByLabel("Testing observations", { exact: true })
    .fill("I tried Result 1: keyboard works; narrow view not tested.");
  await page.getByRole("button", { name: "Save testing", exact: true }).click();
  await expect(
    page.getByText("Human testing · Result 1", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Tested result", exact: true }),
  ).toHaveAttribute("href", /conversation\/result%3A/);
  expect(await (await request.get(`${path}/tasks/try/results`)).json()).toEqual(
    beforeTesting,
  );
  await page.reload();
  await expect(
    page.getByText(
      "I tried Result 1: keyboard works; narrow view not tested.",
      { exact: true },
    ),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Request changes", exact: true })
    .click();
  const feedback = page.getByLabel("Feedback for this result", { exact: true });
  await expect(feedback).toBeFocused();
  await page
    .getByRole("button", {
      name: "Ask without requesting changes",
      exact: true,
    })
    .click();
  const message = page.getByRole("textbox", {
    name: "Message the worker",
    exact: true,
  });
  await expect(message).toBeVisible();
  await message.fill("A question, not a change request");
  await message.fill("");
  await expect(message).toBeVisible();
  await page
    .getByRole("button", { name: "Cancel message", exact: true })
    .click();
  await expect(message).toHaveCount(0);
  await page
    .getByRole("button", { name: "Request changes", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Cancel feedback", exact: true })
    .click();
  await expect(feedback).toHaveCount(0);
  await page
    .getByRole("button", { name: "Request changes", exact: true })
    .click();
  await feedback.fill("Make the greeting useful.");
  await page
    .getByRole("button", { name: "Send feedback", exact: true })
    .click();
  await expect(
    page.getByText("Changes requested. The worker queue is paused", {
      exact: false,
    }),
  ).toBeVisible();
  fixture("successor");
  await expect(async () => {
    const versions = await (
      await request.get(`${path}/tasks/try/results`)
    ).json();
    expect(versions.items[0]?.status).toBe("ready");
    expect(versions.items[0]?.id).not.toBe(first!.id);
  }).toPass();
  selectedPreview = (
    await (await request.get(`${path}/tasks/try/results`)).json()
  ).items[0].id;
  await page.locator(".entity-overlay-body").evaluate((el) => {
    el.scrollTop = el.scrollHeight;
  });
  await expect(
    preview()
      .locator("summary")
      .filter({ hasText: /^Try result$/ }),
  ).toBeVisible();
  await preview()
    .locator("summary")
    .filter({ hasText: /^Try result$/ })
    .click();
  await expect(
    preview().getByText("IPV-1 · Review 2", { exact: true }),
  ).toBeVisible();
  const second = (await (await request.get(`${path}/tasks/try/results`)).json())
    .items[0];
  const next = await (
    await request.get(`${path}/inspection?result_id=${second.id}`)
  ).json();
  expect(next.workspace).not.toBe(copy.workspace);
  expect(
    execFileSync("/bin/sh", ["-c", next.command], { encoding: "utf8" }),
  ).toContain("Useful successor");
  expect(readFileSync(join(copy.workspace, "app.py"), "utf8")).toContain(
    "Preview edit",
  );
  await page
    .getByRole("button", { name: "Approve and integrate", exact: true })
    .click();
  await expect(
    page.getByText("Delivered to delivery.", { exact: false }),
  ).toBeVisible();
  await page.goto(`/projects/${project}`);
  await expect(
    page.getByRole("button", { name: "Try project", exact: true }),
  ).toHaveCount(0);
  selectedPreview = first!.id;
  await page.goto(`/projects/${project}/tasks/IPV-1/changes/${first!.id}`);
  await preview()
    .locator("summary")
    .filter({ hasText: /^Try result$/ })
    .click();
  await expect(
    preview().getByText("Local changes are present.", { exact: false }),
  ).toBeVisible();
  await expect(
    preview().getByText("A newer result or attempt exists.", { exact: false }),
  ).toBeVisible();
  await preview().getByRole("button", { name: "Prepare another copy" }).click();
  await expect(
    preview().getByText("Local changes are present.", { exact: false }),
  ).toHaveCount(0);
  expect(readFileSync(join(copy.workspace, "app.py"), "utf8")).toContain(
    "Preview edit",
  );
  await page.goto(`/projects/${project}/edit/integration`);
  const runCommand = page.getByLabel("Run command", { exact: true });
  await expect(runCommand).toHaveValue("python app.py");
  await runCommand.fill("python app.py --draft");
  const runSettings = await (
    await request.get(`${path}/inspection/settings`)
  ).json();
  await request.put(`${path}/inspection/settings`, {
    data: {
      expected_revision: runSettings.revision,
      run_command: "python app.py --elsewhere",
    },
  });
  await page
    .getByRole("button", { name: "Save integration settings", exact: true })
    .click();
  await expect(runCommand).toHaveValue("python app.py --draft");
  await expect(
    page.getByRole("button", { name: "Load current run command" }),
  ).toBeVisible();
  page.once("dialog", (dialog) => void dialog.accept());
  await page.getByRole("button", { name: "Load current run command" }).click();
  await expect(runCommand).toHaveValue("python app.py --elsewhere");
  await runCommand.fill("python app.py");
  const runSaved = page.waitForResponse(
    (response) =>
      response.url().endsWith(`${path}/inspection/settings`) &&
      response.request().method() === "PUT",
  );
  await page
    .getByRole("button", { name: "Save integration settings", exact: true })
    .click();
  expect((await runSaved).ok()).toBe(true);
  await expect(
    page.getByRole("button", {
      name: "Save integration settings",
      exact: true,
    }),
  ).toBeDisabled();
  const cleanSettings = await (
    await request.get(`${path}/inspection/settings`)
  ).json();
  const externalUpdate = await request.put(`${path}/inspection/settings`, {
    data: {
      expected_revision: cleanSettings.revision,
      run_command: "python app.py --updated",
    },
  });
  expect(externalUpdate.ok()).toBe(true);
  await expect(runCommand).toHaveValue("python app.py --updated");
});

test("outcomes finish reports, preserve partial work and offer one contextual remedy", async ({
  page,
  request,
}) => {
  test.setTimeout(90000);
  execFileSync("uv", [
    "run",
    "--project",
    checkout,
    "python",
    join(checkout, "tests/outcome_browser_fixture.py"),
    state,
  ]);
  const result = async (kind: string) =>
    (
      await (
        await request.get(`/api/projects/outcome-${kind}/tasks/work/results`)
      ).json()
    ).items[0];
  await expect(async () =>
    expect((await result("report"))?.status).toBe("delivered"),
  ).toPass();
  // Stagger independently loaded records, including content taller than the viewport.
  let resultDelay = 0;
  let heldReport: Promise<void> | null = null;
  await page.route(
    "**/api/projects/outcome-report/results/*",
    async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      if (heldReport) await heldReport;
      body.report.summary += "\n\n" + "A detailed finding.\n\n".repeat(35);
      await new Promise((resolve) => setTimeout(resolve, resultDelay));
      await route.fulfill({ response, json: body });
    },
  );
  await page.route(
    (url) =>
      decodeURIComponent(url.pathname).includes(
        "/projects/outcome-report/tasks/work/thread/result:",
      ),
    async (route) => {
      await new Promise((resolve) =>
        setTimeout(resolve, resultDelay ? 0 : 500),
      );
      await route.continue();
    },
  );
  const reportPane = page.locator(".entity-overlay-body");
  const reportAtBottom = () =>
    reportPane.evaluate(
      (el) => el.scrollHeight - el.scrollTop - el.clientHeight < 3,
    );
  for (const delay of [0, 250]) {
    resultDelay = delay;
    await page.goto("/projects/outcome-report/inbox");
    // Use the real history card and its versioned result URL.
    await page
      .getByRole("region", { name: "History", exact: true })
      .getByRole("link", { name: /Report outcome/ })
      .click();
    await expect(
      page.getByRole("region", { name: "Worker report", exact: true }),
    ).toContainText("A detailed finding.");
    await expect.poll(reportAtBottom).toBe(true);
    await page
      .getByRole("button", { name: "Close editor", exact: true })
      .click();
  }
  // Board opens still follow late layout, while an explicit timestamp targets the entry.
  await page.goto("/projects/outcome-report");
  await page
    .getByRole("link", { name: "ORP-1 Report outcome", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "Worker report", exact: true }),
  ).toContainText("A detailed finding.");
  await expect.poll(reportAtBottom).toBe(true);
  const reportStamp = page
    .locator('[data-kind="result"]')
    .getByRole("link", { name: "Permalink: Result 1", exact: true });
  const reportHref = await reportStamp.getAttribute("href");
  let releaseReport!: () => void;
  heldReport = new Promise<void>((resolve) => {
    releaseReport = resolve;
  });
  await page.goto(reportHref!);
  await expect(
    page.getByRole("region", { name: "Proposed result", exact: true }),
  ).toBeAttached();
  await expect(reportStamp).toBeInViewport();
  const startingViewport = page.viewportSize()!;
  // A short placeholder clamps the scroll position when the viewport grows.
  // That browser adjustment must not change an explicit link into follow mode.
  await page.setViewportSize({
    ...startingViewport,
    height: startingViewport.height + 200,
  });
  releaseReport();
  heldReport = null;
  await expect(
    page.getByRole("region", { name: "Worker report", exact: true }),
  ).toContainText("A detailed finding.");
  await expect(reportStamp).toBeInViewport();
  await expect.poll(reportAtBottom).toBe(false);
  const originalViewport = startingViewport;
  await page.setViewportSize({ width: 600, height: 500 });
  await expect(reportStamp).toBeInViewport();
  await page.setViewportSize(originalViewport);
  await page.goto("/projects/outcome-report/tasks/ORP-1/changes");
  await expect(
    page.getByText(
      "Findings delivered; this does not endorse the recommendation.",
      { exact: false },
    ),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Approve and integrate" }),
  ).toHaveCount(0);
  expect((await result("report")).approved_at).toBeNull();
  await expect(async () =>
    expect((await result("partial"))?.problem_code).toBe("partial_outcome"),
  ).toPass();
  await page.goto("/projects/outcome-partial/tasks/OPT-1/changes");
  await expect(
    page.getByRole("heading", { name: "Work remaining" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Approve and integrate" }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Continue work", exact: true })
    .click();
  await page
    .getByLabel("Feedback for this result", { exact: true })
    .fill("Finish the second behavior");
  await page
    .getByRole("button", { name: "Send feedback", exact: true })
    .click();
  await expect(
    page.getByText("Changes requested. The worker queue is paused", {
      exact: false,
    }),
  ).toBeVisible();
  expect((await result("partial")).feedback).toBe("Finish the second behavior");
  await expect(async () =>
    expect((await result("checks"))?.problem_code).toBe("checks_failed"),
  ).toPass();
  await page.goto("/projects/outcome-checks/tasks/OCK-1/changes");
  const recovery = page.getByRole("region", { name: "Result recovery" });
  const actions = page.getByRole("group", {
    name: "Task actions",
    exact: true,
  });
  await expect(
    actions.getByRole("button", { name: "Request correction" }),
  ).toBeVisible();
  await expect(recovery).toBeVisible();
  await expect(
    actions.getByRole("button", { name: "Archive", exact: true }),
  ).toBeVisible();
  await expect(recovery.getByRole("link")).toHaveCount(0);
  const taskPath = "/api/projects/outcome-checks/tasks/work";
  const originalTask = await (await request.get(taskPath)).json();
  expect(
    (
      await request.put(taskPath, {
        data: {
          expected_revision: originalTask.revision,
          body: "Deliver the revised agreed outcome",
        },
      })
    ).ok(),
  ).toBe(true);
  await expect(
    actions.getByRole("button", { name: "Request correction" }),
  ).toHaveCount(0);
  const revisedTask = await (await request.get(taskPath)).json();
  expect(
    (
      await request.post(taskPath + "/reconcile", {
        data: {
          expected_revision: revisedTask.revision,
          expected_decision_sequence: revisedTask.decision_sequence,
          completion: "code",
          note: "Human clarified the remaining outcome",
        },
      })
    ).ok(),
  ).toBe(true);
  await expect(
    actions.getByRole("button", { name: "Request correction" }),
  ).toBeVisible();
  await actions.getByRole("button", { name: "Request correction" }).click();
  await expect(
    page.getByText("Changes requested. The worker queue is paused", {
      exact: false,
    }),
  ).toBeVisible();
  await expect(async () =>
    expect((await result("setup"))?.problem_code).toBe("runtime_setup_failed"),
  ).toPass();
  await page.goto("/projects/outcome-setup/tasks/OST-1/changes");
  await actions.getByRole("link", { name: "Fix project setup" }).click();
  await expect(page).toHaveURL(/edit\/integration/);
});

test("attempt activity updates without reloading the board and replays on reload", async ({
  page,
}) => {
  const fixture = (op: string) =>
    JSON.parse(
      execFileSync(
        "uv",
        [
          "run",
          "--project",
          checkout,
          "python",
          join(checkout, "tests/activity_browser_fixture.py"),
          state,
          op,
        ],
        { encoding: "utf8" },
      ),
    );
  const seeded = fixture("create");
  await page.goto("/projects/stream-project/tasks/STR-1");
  const output = page.getByRole("region", {
    name: "Worker activity",
    exact: true,
  });
  await expect(output).toContainText("create observed output");
  await expect(
    page.getByLabel("Message the worker", { exact: true }),
  ).toHaveCount(0);
  let boards = 0;
  page.on("request", (req) => {
    if (req.url().endsWith("/view/board")) boards++;
  });
  fixture("later");
  await expect(output).toContainText("later observed output");
  expect(boards).toBe(0);
  await page.reload();
  await expect(output).toContainText("create observed output");
  await expect(output).toContainText("later observed output");
  await expect(
    page.locator(".task-conversation-header .work-state"),
  ).toContainText("Working");
  fixture("long");
  await expect(output).toContainText("long observed output");
  const atEnd = () =>
    output.evaluate(
      (el) => el.scrollHeight - el.scrollTop - el.clientHeight < 3,
    );
  await expect.poll(atEnd).toBe(true);
  await output.evaluate((el) => el.scrollTo({ top: el.scrollHeight / 3 }));
  await expect.poll(atEnd).toBe(false);
  const before = await output.evaluate((el) => el.scrollTop);
  fixture("while-reading");
  await expect(output).toContainText("while-reading observed output");
  await expect.poll(() => output.evaluate((el) => el.scrollTop)).toBe(before);
  await output.evaluate((el) => el.scrollTo({ top: el.scrollHeight }));
  await expect.poll(atEnd).toBe(true);
  fixture("following-again");
  await expect(output).toContainText("following-again observed output");
  await expect.poll(atEnd).toBe(true);
  await expect(
    page.getByRole("button", { name: "Follow latest activity" }),
  ).toHaveCount(0);
  fixture("compact");
  const script = output.locator('[data-kind="command"]');
  await expect(script).toContainText("Exit code: 17");
  await expect(script.locator("pre").first()).not.toContainText(
    "retained source",
  );
  await script.getByText("Retained output", { exact: true }).click();
  await expect(script.locator("details pre")).toContainText("retained source");
  await expect(output.locator('[data-kind="agent"] > div > pre')).toContainText(
    "Code block collapsed",
  );
  await expect(page.getByLabel("Reported token usage").first()).toContainText(
    "240 tokens reported so far · 100 cached input",
  );
  const attempt = page.locator('[data-kind="attempt"]');
  await expect(
    attempt.getByText("Execution details", { exact: true }),
  ).toHaveCount(0);
  await expect(
    attempt.getByRole("region", { name: "Execution details", exact: true }),
  ).toHaveCount(0);
  await expect(page).toHaveURL(/STR-1$/);
  await page.goto(
    "/projects/stream-project/tasks/STR-1/history/worker:" + seeded.run_id,
  );
  await expect(
    attempt.getByRole("region", { name: "Execution details", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Stop worker", exact: true }).click();
  const stopConfirmation = page.getByRole("alertdialog", {
    name: "Stop this worker?",
    exact: true,
  });
  await expect(stopConfirmation).toBeVisible();
  await stopConfirmation
    .getByRole("button", { name: "Cancel", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Stop worker", exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "Retry worker", exact: true }),
  ).toHaveCount(0);
  await page.route(
    `**/api/projects/stream-project/runs/${seeded.run_id}/stop`,
    async (route) => {
      fixture("stop-race"); // Progress arrives after confirmation serialized its attempt revision.
      await route.continue();
    },
  );
  await page.getByRole("button", { name: "Stop worker", exact: true }).click();
  await stopConfirmation
    .getByRole("button", { name: "Stop worker", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Retry worker", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Archive", exact: true }),
  ).toBeDisabled();
  fixture("discussion");
  await page.reload();
  const reply = page
    .locator('[data-kind="attempt"]')
    .filter({ hasText: "Worker reply" });
  await expect(reply).toContainText("A focused answer to your question.");
  await expect(reply).toContainText("No runtime test was needed.");
  await expect(
    reply.getByRole("region", { name: "Worker activity", exact: true }),
  ).toBeHidden();
  await expect(reply.getByLabel("Reported token usage")).toContainText(
    "750 tokens reported",
  );
  await expect(
    reply.getByText("Checks and delivery", { exact: true }),
  ).toHaveCount(0);
  await reply.getByText("Worker activity", { exact: true }).click();
  await expect(
    reply.getByRole("region", { name: "Worker activity", exact: true }),
  ).toContainText("Reading selected evidence");
  await expect(
    reply.getByText("Execution details", { exact: true }),
  ).toHaveCount(0);
  await expect(reply.getByText("Inspected", { exact: true })).toHaveCount(0);
  await expect(reply.getByText("Code changes", { exact: true })).toHaveCount(0);
});

test("browser notifications are opt-in, deduplicate across tabs and link to a task", async ({
  page,
  request,
  context,
}) => {
  await context.addInitScript(() => {
    const notices: {
      title: string;
      options: NotificationOptions;
      onclick?: () => void;
      close: () => void;
    }[] = [];
    Object.defineProperty(window, "demoNotices", { value: notices });
    class TestNotification {
      static permission = "granted";
      static async requestPermission() {
        this.permission = "granted";
        return "granted";
      }
      constructor(
        public title: string,
        public options: NotificationOptions,
      ) {
        notices.push(this);
      }
      close() {}
    }
    Object.defineProperty(window, "Notification", { value: TestNotification });
    Object.defineProperty(document, "visibilityState", { get: () => "hidden" });
  });
  await request.post("/api/projects/initialize", {
    data: {
      path: existingDirectory(join(state, "notify-project")),
      task_prefix: "BNF",
    },
  });
  await request.post("/api/projects/notify-project/tasks", {
    data: { id: "notice", title: "Needs a choice" },
  });
  await page.goto("/projects/notify-project");
  await page.getByRole("button", { name: /Notifications/ }).click();
  await page
    .getByRole("button", { name: "Enable browser notifications", exact: true })
    .click();
  await expect
    .poll(() =>
      request
        .get("/api/notifications/settings")
        .then(async (r) => (await r.json()).browser_enabled),
    )
    .toBe(true);
  await request.post("/api/projects/notify-project/questions", {
    data: {
      id: "notify-choice",
      task_id: "notice",
      question: "Which behavior?",
      context: "A meaningful choice.",
      recommendation: "Keep it small.",
      blocking_scope: "Behavior",
    },
  });
  await expect
    .poll(
      () =>
        page.evaluate(
          () =>
            (window as unknown as { demoNotices: unknown[] }).demoNotices
              .length,
        ),
      { timeout: 10000 },
    )
    .toBe(1);
  const second = await context.newPage();
  const secondRead = second.waitForResponse((response) =>
    response.url().endsWith("/api/notifications/browser/claim"),
  );
  await second.goto("/projects/notify-project");
  await secondRead;
  expect(
    await second.evaluate(
      () =>
        (window as unknown as { demoNotices: unknown[] }).demoNotices.length,
    ),
  ).toBe(0);
  await expect
    .poll(async () =>
      (await (await request.get("/api/notifications")).json()).items.some(
        (item: { key: string }) => item.key.includes("notify-choice"),
      ),
    )
    .toBe(true);
  expect(
    await second.evaluate(() =>
      localStorage.getItem("flowfield-attention-receipts"),
    ),
  ).toBeNull();
  await page.evaluate(() =>
    (
      window as unknown as { demoNotices: { onclick: () => void }[] }
    ).demoNotices[0].onclick(),
  );
  await expect(page).toHaveURL(
    /tasks\/BNF-1\/conversation\/question:notify-choice:1/,
  );
  await page.reload();
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (window as unknown as { demoNotices: unknown[] }).demoNotices.length,
      ),
    )
    .toBe(0);
  await second.close();
});

test("task feed follows the live end but preserves reading position and timestamp targets", async ({
  page,
  request,
}) => {
  await request.post("/api/projects/initialize", {
    data: {
      path: existingDirectory(join(state, "feed-reading")),
      task_prefix: "FED",
    },
  });
  const path = "/api/projects/feed-reading";
  await request.post(`${path}/tasks`, {
    data: {
      id: "one",
      title: "Read a growing feed",
      body: "Keep the reader oriented.",
    },
  });
  for (let i = 0; i < 12; i++) {
    await request.post(`${path}/activity`, {
      data: {
        task_id: "one",
        body: `Observation ${i}\n\n${"Useful detail. ".repeat(40)}`,
      },
    });
  }
  await page.goto("/projects/feed-reading/tasks/FED-1");
  const pane = page.locator(".entity-overlay-body");
  const feed = page.getByRole("list", { name: "Task feed" });
  const atBottom = () =>
    pane.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight < 3);
  await expect(feed).toContainText("Observation 11");
  await expect.poll(atBottom).toBe(true);
  await request.post(`${path}/activity`, {
    data: { task_id: "one", body: "Newest while following" },
  });
  await expect(feed).toContainText("Newest while following");
  await expect.poll(atBottom).toBe(true);
  const anchor = feed.locator('[data-kind="activity"]').nth(5);
  await anchor.evaluate((el) => el.scrollIntoView({ block: "start" }));
  await expect.poll(atBottom).toBe(false);
  const position = () =>
    anchor.evaluate(
      (el) =>
        el.getBoundingClientRect().top -
        el.closest(".entity-overlay-body")!.getBoundingClientRect().top,
    );
  const before = await position();
  await request.post(`${path}/activity`, {
    data: { task_id: "one", body: "Newest while reading earlier work" },
  });
  await expect(feed).toContainText("Newest while reading earlier work");
  // This checks scroll anchoring, not a fixed layout or pixel styling contract.
  await expect
    .poll(async () => Math.abs((await position()) - before) < 2)
    .toBe(true);
  await expect.poll(atBottom).toBe(false);
  await pane.evaluate((el) => {
    el.scrollTop = el.scrollHeight;
  });
  await expect.poll(atBottom).toBe(true);
  await request.post(`${path}/activity`, {
    data: { task_id: "one", body: "Newest after returning to the bottom" },
  });
  await expect(feed).toContainText("Newest after returning to the bottom");
  await expect.poll(atBottom).toBe(true);
  const stamp = anchor.getByRole("link", {
    name: "Permalink: Note",
    exact: true,
  });
  const href = await stamp.getAttribute("href");
  await page.goto(href!);
  await expect(anchor).toBeInViewport();
  await expect(
    page.getByRole("button", { name: "Close editor", exact: true }),
  ).toBeInViewport();
  await expect(feed.locator(".detail-entry-title a")).toHaveCount(0);
});

test("legacy task-question links resolve into the feed and preserve answers", async ({
  page,
  request,
}) => {
  await request.post("/api/projects/initialize", {
    data: {
      path: existingDirectory(join(state, "feed-question")),
      task_prefix: "FQN",
    },
  });
  const path = "/api/projects/feed-question";
  await request.post(`${path}/tasks`, {
    data: { id: "one", title: "A question in its task" },
  });
  await request.post(`${path}/questions`, {
    data: {
      id: "choice",
      task_id: "one",
      question: "Which format?",
      context: "Choose an output.",
      recommendation: "JSON",
      choices: ["JSON", "CSV"],
      blocking_scope: "Format",
    },
  });
  await page.goto("/projects/feed-question/inbox/choice");
  await expect(page).toHaveURL(
    /tasks\/FQN-1\/conversation\/question%3Achoice$/,
  );
  await expect(
    page.getByRole("dialog", { name: "Question", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Waiting on", exact: true }),
  ).toHaveCount(0);
  const answer = page.getByLabel("Your answer", { exact: true });
  await expect(answer).not.toHaveAttribute("placeholder");
  await expect(
    page.getByRole("button", { name: "JSON", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "CSV", exact: true }),
  ).toHaveCount(0);
  await answer.fill("JSON");
  await page.getByRole("button", { name: "Send answer", exact: true }).click();
  await expect(page.locator('[data-kind="answer"]')).toContainText("JSON");
  const question = await (await request.get(`${path}/questions/choice`)).json();
  expect(question.answer).toBe("JSON");
  await page.goto(
    "/projects/feed-question/tasks/FQN-1/related/questions/choice",
  );
  await expect(page).toHaveURL(/conversation\/question%3Achoice$/);
  await expect(page.locator('[data-kind="answer"]')).toContainText("JSON");
});

test("archive confirmation retains its selected revision across live changes", async ({
  page,
  request,
}) => {
  await request.post("/api/projects/initialize", {
    data: {
      path: existingDirectory(join(state, "archive-confirmation")),
      task_prefix: "ACF",
    },
  });
  const path = "/api/projects/archive-confirmation";
  const task = await (
    await request.post(path + "/tasks", {
      data: {
        id: "one",
        title: "Confirm the selected task",
        body: "Original agreement",
      },
    })
  ).json();
  await page.goto("/projects/archive-confirmation/tasks/ACF-1");
  await page.getByRole("button", { name: "Archive", exact: true }).click();
  const confirmation = page.getByRole("alertdialog", {
    name: "Archive ACF-1?",
    exact: true,
  });
  await expect(confirmation).toBeVisible();
  const updated = await request.put(path + "/tasks/one", {
    data: {
      expected_revision: task.revision,
      body: "Revised agreement",
      author: "agent",
    },
  });
  expect(updated.ok()).toBe(true);
  await expect(page.getByLabel("Current task definition")).toContainText(
    "Revised agreement",
  );
  await confirmation
    .getByRole("button", { name: "Archive", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText(/stale/i);
  expect((await (await request.get(path + "/tasks/one")).json()).archived).toBe(
    false,
  );
  await page.getByRole("button", { name: "Archive", exact: true }).click();
  await confirmation
    .getByRole("button", { name: "Archive", exact: true })
    .click();
  await expect(
    page.getByRole("dialog", { name: "Task details", exact: true }),
  ).toHaveCount(0);
  expect((await (await request.get(path + "/tasks/one")).json()).archived).toBe(
    true,
  );
});

test("sidebar names disclose only clipped text and idle input opens deliberately", async ({
  page,
  request,
}) => {
  const name = "A carefully tended collection of pocket gardens";
  await request.post("/api/projects/initialize", {
    data: {
      path: existingDirectory(join(state, "sidebar-names")),
      name,
      task_prefix: "SBN",
    },
  });
  await request.post("/api/projects/initialize", {
    data: { path: existingDirectory(join(state, "short-name")), name: "Short" },
  });
  const path = "/api/projects/sidebar-names";
  await request.post(path + "/tasks", {
    data: {
      id: "idle",
      title: "Read the current agreement",
      body: "Useful intent",
    },
  });
  const question = await request.post(path + "/questions", {
    data: {
      question: "Which outcome matters most?",
      context: "Choose the next project priority.",
      recommendation: "Discuss the priorities with the coordinator.",
      author: "agent",
    },
  });
  expect(question.ok()).toBe(true);
  await page.goto("/projects/sidebar-names");
  const sidebar = page.getByRole("navigation", {
    name: "Projects",
    exact: true,
  });
  const short = sidebar.getByRole("link", { name: "Short", exact: true });
  await short.hover();
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  const long = sidebar.getByRole("link", { name, exact: true });
  await long.hover();
  await expect(page.getByRole("tooltip")).toHaveText(name);
  await page.keyboard.press("Escape");
  await long.focus();
  await expect(page.getByRole("tooltip")).toHaveText(name);
  await page.keyboard.press("Escape");
  // A wider mobile sidebar fits this name: the redundant tooltip disappears.
  await page.setViewportSize({ width: 650, height: 844 });
  await short.hover();
  await long.hover();
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  await expect(
    sidebar.getByRole("button", { name: /^Needs you 1$/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: /item needs your attention/ }),
  ).toHaveCount(0);
  await page.getByRole("link", { name: /Read the current agreement/ }).click();
  await expect(page.getByLabel("Current task definition")).toContainText(
    "Definition",
  );
  await expect(
    page.getByLabel("Message the worker", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Latest", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Ask worker", exact: true }).click();
  await expect(
    page.getByLabel("Message the worker", { exact: true }),
  ).toBeFocused();
  await page
    .getByLabel("Message the worker", { exact: true })
    .fill("Explain this outcome");
  await page
    .getByRole("button", { name: "Cancel message", exact: true })
    .click();
  await expect(
    page.getByLabel("Message the worker", { exact: true }),
  ).toHaveCount(0);
  expect(
    (await (await request.get(path + "/tasks/idle/thread")).json()).items.some(
      (item: { kind: string }) => item.kind === "reply",
    ),
  ).toBe(false);
});

test("update notifications persist, dismiss across browsers and share manual discovery", async ({
  page,
  request,
  browser,
}) => {
  const observe = (version: string) =>
    execFileSync("uv", [
      "run",
      "--project",
      "..",
      "../tests/notification_browser_fixture.py",
      state,
      version,
    ]);
  observe("9.9.1");
  let manualChecks = 0;
  let startups = 0;
  await page.route("**/api/updates/check", async (route) => {
    if (route.request().postDataJSON().reason === "manual") {
      manualChecks++;
      observe(manualChecks === 1 ? "9.9.1" : "9.9.2");
    } else startups++;
    await route.fulfill({
      json: await (await request.get("/api/updates")).json(),
    });
  });
  await page.goto("/");
  await expect.poll(() => startups).toBeGreaterThan(0);
  await expect(
    page.getByRole("dialog", { name: "Notifications", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: /Notifications/ }).click();
  const sheet = page.getByRole("dialog", {
    name: "Notifications",
    exact: true,
  });
  const notice = sheet
    .getByRole("alert")
    .filter({ hasText: "Flowfield 9.9.1 is available" });
  await expect(notice).toBeVisible();
  await expect(
    notice.getByRole("link", { name: "Release notes" }),
  ).toHaveAttribute(
    "href",
    "https://github.com/flowfield-sh/flowfield-core/releases",
  );
  await expect(notice).toContainText("uv tool upgrade flowfield-core");
  await expect(notice).toContainText(
    "python -m pip install --upgrade flowfield-core",
  );
  await page.screenshot({
    path: "test-results/persistent-update-notification.png",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(notice).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect(
    await notice.evaluate((card) => {
      const bounds = card.getBoundingClientRect();
      return [...card.querySelectorAll("p, pre")].every((element) => {
        const rect = element.getBoundingClientRect();
        return rect.left >= bounds.left && rect.right <= bounds.right;
      });
    }),
  ).toBe(true);
  await page.screenshot({
    path: "test-results/persistent-update-notification-mobile.png",
  });
  await notice
    .getByRole("button", { name: "Dismiss Flowfield 9.9.1 is available" })
    .click();
  await expect(notice).toHaveCount(0);
  await sheet
    .getByRole("button", { name: "Check for updates", exact: true })
    .click();
  await expect.poll(() => manualChecks).toBe(1);
  await expect(notice).toHaveCount(0);
  await page.reload();
  await page.getByRole("button", { name: /Notifications/ }).click();
  await expect(notice).toHaveCount(0);
  const secondContext = await browser.newContext();
  const second = await secondContext.newPage();
  await second.goto("/");
  await second.getByRole("button", { name: /Notifications/ }).click();
  await expect(
    second.getByRole("heading", { name: "Flowfield 9.9.1 is available" }),
  ).toHaveCount(0);
  await sheet
    .getByRole("button", { name: "Check for updates", exact: true })
    .click();
  await expect.poll(() => manualChecks).toBe(2);
  await expect(
    sheet.getByRole("heading", { name: "Flowfield 9.9.2 is available" }),
  ).toBeVisible();
  await expect(
    second.getByRole("heading", { name: "Flowfield 9.9.2 is available" }),
  ).toBeVisible();
  await sheet
    .getByRole("button", { name: "Clear notifications", exact: true })
    .click();
  await expect(
    second.getByRole("heading", { name: "Flowfield 9.9.2 is available" }),
  ).toHaveCount(0);
  await secondContext.close();
});
