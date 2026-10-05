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
  await page.route(`**${historyPath}/${conversation.id}`, (route) =>
    route.fulfill({
      json: { conversation, items: turns, active, next_before: null },
    }),
  );
  await page.route(
    `**${historyPath}/${conversation.id}/messages`,
    async (route) => {
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
    },
  );
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
  active!.activity.items[0].text +=
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
  await page.screenshot({
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
    path: testInfo.outputPath("coordinator-mobile.png"),
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: testInfo.outputPath("coordinator-mobile.png"),
  });
  await input.fill("");
  await page
    .getByRole("button", { name: "New conversation", exact: true })
    .click();
  await expect(
    page
      .getByRole("combobox", { name: "Conversation history" })
      .locator("option"),
  ).toHaveCount(2);
  await expect(input).toHaveValue("");
  await page
    .getByRole("combobox", { name: "Conversation history" })
    .selectOption(conversation.id);
  await expect(
    page.getByText("This conversation is retained history.", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByText("Let’s plan the work.", { exact: false }),
  ).toBeVisible();
  expect(sends).toBe(1);
});

test("conversation settings inherit defaults and retain edits through disclosure and navigation", async ({
  page,
  request,
}) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const directory = join(state, "chat-settings");
  mkdirSync(directory, { recursive: true });
  await request.post("/api/projects/initialize", {
    data: { path: directory, name: "Chat Settings", task_prefix: "CST" },
  });
  const conversation = await (
    await request.post("/api/projects/chat-settings/coordinator")
  ).json();
  await page.route("**/api/worker-models", (route) =>
    route.fulfill({
      json: [
        { id: "first", name: "First", efforts: ["low"], modes: [] },
        { id: "second", name: "Second", efforts: ["high"], modes: [] },
      ],
    }),
  );
  const defaults = {
    harness: "codex",
    model: "first",
    effort: "low",
    mode: null,
  };
  let settings = {
    revision: 1,
    selection: null as typeof defaults | null,
    effective: {
      choice: defaults,
      source: "project",
      default_revision: 1,
      override_revision: 1,
    },
  };
  await page.route(
    `**/api/projects/chat-settings/coordinator/${conversation.id}/settings`,
    async (route) => {
      if (route.request().method() === "PUT") {
        const change = route.request().postDataJSON();
        expect(change.selection?.mode ?? null).toBeNull();
        settings = {
          revision: settings.revision + 1,
          selection: change.selection,
          effective: {
            choice: change.selection ?? defaults,
            source: change.selection ? "override" : "project",
            default_revision: 1,
            override_revision: settings.revision + 1,
          },
        };
      }
      await route.fulfill({ json: settings });
    },
  );
  await page.goto("/projects/chat-settings");
  const controls = page.getByRole("button", { name: "Settings", exact: true });
  await controls.click();
  await expect(
    page.getByText("Using project defaults", { exact: false }),
  ).toBeVisible();
  await page
    .getByRole("combobox", { name: "Model", exact: true })
    .selectOption("second");
  await page
    .getByRole("combobox", { name: "Reasoning effort", exact: true })
    .selectOption("high");
  await controls.click();
  await controls.click();
  await expect(
    page.getByRole("combobox", { name: "Model", exact: true }),
  ).toHaveValue("second");
  await page.getByRole("link", { name: "Flowfield", exact: true }).click();
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await page.getByRole("button", { name: "Keep editing", exact: true }).click();
  await page
    .getByRole("button", { name: "Save agent settings", exact: true })
    .click();
  await expect(
    page.getByText("Conversation override", { exact: false }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Use project defaults", exact: true })
    .click();
  await expect(
    page.getByRole("combobox", { name: "Model", exact: true }),
  ).toHaveValue("first");
  await page.getByRole("link", { name: "Flowfield", exact: true }).click();
  await expect(page).toHaveURL(/\/$/);
});
