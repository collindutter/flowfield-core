import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import type { components } from "../src/api-schema";

type Turn = components["schemas"]["CoordinatorTurn"];
const state = process.env.FLOWFIELD_SMOKE_STATE!;

test("coordinator streams, stops, retains history and drafts beside responsive work", async ({
  page,
  request,
}, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  for (const id of ["chat-browser", "chat-other"]) {
    const directory = join(state, id);
    mkdirSync(directory, { recursive: true });
    expect(
      (
        await request.post("/api/projects/initialize", {
          data: {
            path: directory,
            name: id === "chat-browser" ? "Chat Browser" : "Chat Other",
            task_prefix: id === "chat-browser" ? "CHT" : "CHO",
          },
        })
      ).ok(),
    ).toBeTruthy();
  }
  expect(
    (
      await request.post("/api/projects/chat-browser/tasks", {
        data: { title: "Plan the chat experience" },
      })
    ).ok(),
  ).toBeTruthy();
  const historyPath = "/api/projects/chat-browser/coordinator";
  const first = (await request.post(historyPath)).json();
  const conversation = await first;
  const turns: Turn[] = [];
  let active: Turn | null = null;
  let sends = 0;
  await page.route(`**${historyPath}{,?*}`, (route) =>
    route.fulfill({
      json: { conversation, items: turns, active, next_before: null },
    }),
  );
  await page.route(`**${historyPath}/messages`, async (route) => {
    sends++;
    const message = route.request().postDataJSON();
    const turn: Turn = {
      ...message,
      number: sends,
      project_id: "chat-browser",
      conversation_id: conversation.id,
      created_at: new Date().toISOString(),
      status: "running",
      native_started: true,
      notice: "",
      settings: {
        choice: {
          harness: "codex",
          model: "test-model",
          effort: "low",
          mode: null,
        },
        source: "project",
        default_revision: 1,
        override_revision: 1,
      },
      applied: null,
      activity: {
        revision: 1,
        supported: true,
        active: true,
        changed: true,
        omitted: false,
        items: [
          {
            key: "tool",
            kind: "tool",
            text: "Read project · completed\n/project/README.md",
            preview: "Read project · completed\n/project/README.md",
            omitted: false,
            abridged: false,
          },
          {
            key: "reply",
            kind: "agent",
            text: "Let’s plan the work.",
            omitted: false,
            preview: "",
            abridged: false,
          },
        ],
        usage: {
          input_tokens: null,
          output_tokens: null,
          cached_input_tokens: null,
          reasoning_output_tokens: null,
          total_tokens: null,
        },
      },
    };
    turns.push(turn);
    active = turn;
    await route.fulfill({ status: 202, json: turn });
  });
  await page.route(`**${historyPath}/turns/*/stop`, async (route) => {
    if (active) {
      active.status = "stopped";
      active.notice =
        "Stopped. Saved task changes remain; workers continue independently.";
    }
    const saved = active;
    active = null;
    await route.fulfill({ json: saved });
  });
  await page.route("**/api/worker-models*", (route) =>
    route.fulfill({
      json: [{ id: "test-model", name: "Test", efforts: ["low"], modes: [] }],
    }),
  );
  await page.route(
    "**/api/projects/chat-browser/coordinator-settings",
    (route) =>
      route.fulfill({
        json: {
          revision: 1,
          selection: {
            harness: "codex",
            model: "test-model",
            effort: "low",
            mode: null,
          },
          effective: {
            choice: {
              harness: "codex",
              model: "test-model",
              effort: "low",
              mode: null,
            },
            source: "project",
            default_revision: 1,
            override_revision: null,
          },
        },
      }),
  );
  await page.goto("/projects/chat-browser");
  await expect(
    page.getByRole("heading", { name: "Coordinator Chat" }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: /CHT-1.*Plan the chat experience/ }),
  ).toBeVisible();
  const input = page.getByRole("textbox", { name: "Message coordinator" });
  await input.fill("Keep my project draft");
  await page.getByRole("link", { name: "Chat Other", exact: true }).click();
  await page.getByRole("link", { name: "Chat Browser", exact: true }).click();
  await expect(input).toHaveValue("Keep my project draft");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByText("Let’s plan the work.")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Send", exact: true }),
  ).toBeDisabled();
  active!.activity.items.find((item) => item.kind === "agent")!.text +=
    " Open [CHT-1](/projects/chat-browser/tasks/CHT-1).";
  await expect(
    page
      .getByRole("region", { name: "Coordinator conversation" })
      .getByRole("link", { name: "CHT-1" }),
  ).toBeVisible();
  await input.fill("A draft for the next turn");
  await page.getByRole("button", { name: "Stop", exact: true }).click();
  await expect(
    page.getByText(
      "Stopped. Saved task changes remain; workers continue independently.",
    ),
  ).toBeVisible();
  await expect(input).toHaveValue("A draft for the next turn");
  await page.getByText("Activity", { exact: true }).click();
  await expect(page.getByLabel("Tool", { exact: true })).toBeVisible();
  await expect(
    page.getByText("Read project · completed", { exact: false }),
  ).toContainText("/project/README.md");
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("coordinator-desktop.png"),
  });
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(input).toHaveCSS("color", "rgb(227, 231, 236)");
  await page.screenshot({
    path: testInfo.outputPath("coordinator-dark.png"),
  });
  await page.emulateMedia({ colorScheme: "light" });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("tab", { name: "Coordinator", exact: true }).click();
  await expect(input).toBeVisible();
  await page.getByRole("tab", { name: "Work", exact: true }).click();
  await expect(input).not.toBeVisible();
  await page.getByRole("tab", { name: "Coordinator", exact: true }).click();
  await expect(input).toHaveValue("A draft for the next turn");
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("coordinator-mobile.png"),
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("coordinator-mobile.png"),
  });
  await page.reload();
  await page.getByRole("tab", { name: "Coordinator", exact: true }).click();
  await expect(
    page.getByText("Let’s plan the work.", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "New conversation", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("combobox", { name: "Conversation history" }),
  ).toHaveCount(0);
  expect(sends).toBe(1);
});

test("single coordinator requires a saved model, labels loading and retains unsaved edits", async ({
  page,
  request,
}, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const directory = join(state, "chat-settings");
  mkdirSync(directory, { recursive: true });
  await request.post("/api/projects/initialize", {
    data: { path: directory, name: "Chat Settings", task_prefix: "CST" },
  });
  let release!: () => void;
  const discovery = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/worker-models*", async (route) => {
    await discovery;
    await route.fulfill({
      json: [
        { id: "first", name: "First", efforts: ["low"], modes: [] },
        { id: "second", name: "Second", efforts: ["high"], modes: [] },
      ],
    });
  });
  type Settings = components["schemas"]["AgentSettingsView"];
  let settings: Settings = { revision: 1, selection: null, effective: null };
  await page.route(
    "**/api/projects/chat-settings/coordinator-settings",
    async (route) => {
      if (route.request().method() === "PUT") {
        const change = route.request().postDataJSON();
        expect(change.selection.mode).toBeNull();
        settings = {
          revision: settings.revision + 1,
          selection: change.selection,
          effective: {
            choice: change.selection,
            source: "project",
            default_revision: settings.revision + 1,
            override_revision: null,
          },
        };
      }
      await route.fulfill({ json: settings });
    },
  );
  let sends = 0;
  await page.route(
    "**/api/projects/chat-settings/coordinator/messages",
    (route) => {
      sends++;
      return route.fulfill({
        status: 409,
        json: {
          error: {
            code: "unavailable",
            message: "Model is unavailable. Reload models.",
          },
        },
      });
    },
  );
  await page.goto("/projects/chat-settings");
  const model = page.getByRole("combobox", { name: "Model", exact: true });
  const effort = page.getByRole("combobox", {
    name: "Reasoning effort",
    exact: true,
  });
  const send = page.getByRole("button", { name: "Send", exact: true });
  const input = page.getByRole("textbox", { name: "Message coordinator" });
  await expect(model).toBeDisabled();
  await expect(model.locator("option:checked")).toHaveText("Loading models…");
  await expect(effort.locator("option:checked")).toHaveText("Loading efforts…");
  await input.fill("Plan something useful");
  await expect(send).toBeDisabled();
  await input.press("Control+Enter");
  expect(sends).toBe(0);
  release();
  await expect(model).toBeEnabled();
  await expect(model.locator("option:checked")).toHaveText("Choose a model");
  await expect(effort).toBeDisabled();
  await expect(effort.locator("option:checked")).toHaveText(
    "Select a model first",
  );
  await model.selectOption("second");
  await expect(effort).toBeEnabled();
  await effort.selectOption("high");
  await expect(send).toBeDisabled();
  await page.getByRole("link", { name: "Flowfield", exact: true }).click();
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await page.getByRole("button", { name: "Keep editing", exact: true }).click();
  await expect(model).toHaveValue("second");
  await page.screenshot({
    path: testInfo.outputPath("coordinator-model-settings.png"),
  });
  await page.getByRole("button", { name: "Save model", exact: true }).click();
  await expect(send).toBeEnabled();
  await expect(model).not.toBeVisible();
  await page
    .getByRole("button", { name: "second · high", exact: true })
    .click();
  await expect(model).toHaveValue("second");
  await expect(
    page.getByRole("button", { name: "Use project defaults" }),
  ).toHaveCount(0);
  await send.click();
  await expect(page.getByRole("alert")).toContainText("Model is unavailable");
  await expect(page.getByRole("button", { name: "Refresh chat" })).toHaveCount(
    0,
  );
  await expect(input).toHaveValue("Plan something useful");
  expect(sends).toBe(1);
  await page.reload();
  await expect(
    page.getByRole("button", { name: "second · high", exact: true }),
  ).toBeVisible();
});

test("long project chat preserves loaded history while live pages advance", async ({
  page,
  request,
}) => {
  const directory = join(state, "long-chat-project");
  mkdirSync(directory, { recursive: true });
  await request.post("/api/projects/initialize", {
    data: { path: directory, task_prefix: "LCH" },
  });
  await page.route("**/api/worker-models*", (route) =>
    route.fulfill({ json: [] }),
  );
  const historyPath = "/api/projects/long-chat-project/coordinator";
  let count = 40;
  const turn = (number: number): Turn => ({
    id: `history-message-${number}`,
    number,
    project_id: "long-chat-project",
    conversation_id: "saved",
    text: `Planning exchange ${number}`,
    created_at: new Date().toISOString(),
    status: "completed",
    native_started: true,
    notice: "",
    settings: {
      choice: { harness: "codex", model: "test", effort: "low", mode: null },
      source: "project",
      default_revision: 1,
      override_revision: null,
    },
    applied: null,
    activity: {
      revision: 1,
      supported: true,
      active: false,
      changed: true,
      omitted: false,
      items: [],
      usage: {
        input_tokens: null,
        output_tokens: null,
        total_tokens: null,
        cached_input_tokens: null,
        reasoning_output_tokens: null,
        cache_write_input_tokens: null,
        complete: false,
      },
    },
  });
  await page.route(`**${historyPath}*`, (route) => {
    const params = new URL(route.request().url()).searchParams;
    const after = Number(params.get("after"));
    if (after)
      return route.fulfill({
        json: {
          items: Array.from(
            { length: Math.min(20, count - after + 1) },
            (_, i) => turn(after + i),
          ),
          active: null,
          next_before: null,
        },
      });
    const before = Number(params.get("before")) || count + 1;
    const start = Math.max(1, before - 20);
    return route.fulfill({
      json: {
        items: Array.from({ length: before - start }, (_, i) =>
          turn(start + i),
        ),
        next_before: start > 1 ? start : null,
        active: null,
        conversation: null,
      },
    });
  });
  await page.goto("/projects/long-chat-project");
  await page.getByRole("button", { name: "Load earlier messages" }).click();
  const messages = page.locator(".coordinator-turn");
  await expect(messages).toHaveCount(40);
  count = 41;
  await request.post("/api/projects/long-chat-project/tasks", {
    data: { title: "Refresh project state" },
  });
  await expect(
    page.getByText("Planning exchange 41", { exact: true }),
  ).toBeAttached();
  await expect(messages).toHaveCount(41);
  await expect(
    page.getByText("Planning exchange 21", { exact: true }),
  ).toBeAttached();
  await expect(
    page.getByRole("button", { name: "Load earlier messages" }),
  ).toHaveCount(0);
  count = 100; // More than a page arrived while this browser was disconnected.
  await request.post("/api/projects/long-chat-project/tasks", {
    data: { title: "Reconnect with newer work" },
  });
  await expect(messages).toHaveCount(100);
  await expect(
    page.getByText("Planning exchange 60", { exact: true }),
  ).toBeAttached();
});
