import { join } from "node:path";
import { expect, type Page, type APIRequestContext } from "@playwright/test";
import { test, state, existingDirectory, fixtureStages } from "./support";
import type { Artifact } from "../src/RunMedia";

const created = "2026-06-01T12:00:00Z";

async function setup(page: Page, request: APIRequestContext, project: string) {
  const initialized = await request.post("/api/projects/initialize", {
    data: {
      path: existingDirectory(join(state, project)),
      task_prefix: {
        "media-gallery": "MGA",
        "media-states": "MST",
        "media-live": "MLI",
      }[project],
    },
  });
  expect(initialized.ok(), await initialized.text()).toBe(true);
  const response = await request.post(`/api/projects/${project}/tasks`, {
    data: {
      title: "Media evidence",
      stages: fixtureStages(),
    },
  });
  expect(response.ok()).toBe(true);
  const task = await response.json();
  const attempt = {
    id: "attempt:current",
    kind: "attempt",
    source_id: "current",
    revision: 1,
    created_at: created,
    author: "service",
    actor: { role: "worker", label: "Worker", identity: "worker" },
    state: { label: "Working", tone: "active", href: null },
    title: "Worker attempt",
    body: "Worker is browsing",
    status: "running",
    run_id: "current",
    result_id: null,
    question_id: null,
    earlier_id: null,
    successor_id: null,
    stages: [],
    stage_changes: [],
    choices: [],
  };
  await page.route(
    `**/api/projects/${project}/tasks/${task.id}/thread**`,
    (route) => {
      const path = decodeURIComponent(new URL(route.request().url()).pathname);
      return route.fulfill({
        json: path.endsWith("/thread")
          ? { items: [attempt], next_cursor: null }
          : path.includes("attempt:previous")
            ? {
                ...attempt,
                id: "attempt:previous",
                source_id: "previous",
                run_id: "previous",
                status: "completed",
              }
            : path.includes("attempt:")
              ? attempt
              : {
                  ...attempt,
                  id: "definition:1",
                  kind: "definition",
                  source_id: task.id,
                  title: "Task created",
                  body: "",
                  status: null,
                },
      });
    },
  );
  await page.route(
    `**/api/projects/${project}/tasks/${task.id}/input-eligibility`,
    (route) =>
      route.fulfill({
        json: {
          enabled: false,
          reason: "busy",
          task_revision: task.revision,
          agreement_revision: task.agreement_revision,
          run_id: "current",
          result_id: null,
          question_id: null,
          question_revision: null,
          result_revision: null,
          pending_reply_id: null,
        },
      }),
  );
  await page.route(`**/api/projects/${project}/runs/*/activity`, (route) =>
    route.fulfill({
      json: {
        items: [],
        supported: false,
        active: true,
        omitted: false,
      },
    }),
  );
  const artifacts: Artifact[] = [
    {
      id: "image",
      project_id: project,
      task_id: task.id,
      run_id: "previous",
      name: "screen.png",
      title: "Earlier screenshot",
      description: "Previous attempt output",
      mime: "image/png",
      size: 40,
      created_at: created,
      href: `/api/projects/${project}/artifacts/image`,
    },
    {
      id: "video",
      project_id: project,
      task_id: task.id,
      run_id: "current",
      name: "walkthrough.webm",
      title: "Walkthrough",
      description: "Recorded workflow",
      mime: "video/webm",
      size: 80,
      created_at: created,
      href: `/api/projects/${project}/artifacts/video`,
    },
    {
      id: "mp4",
      project_id: project,
      task_id: task.id,
      run_id: "current",
      name: "clip.mp4",
      title: "",
      description: "",
      mime: "video/mp4",
      size: 80,
      created_at: created,
      href: `/api/projects/${project}/artifacts/mp4`,
    },
  ];
  await page.route(
    `**/api/projects/${project}/tasks/${task.id}/artifacts`,
    (route) => route.fulfill({ json: artifacts }),
  );
  await page.route(`**/api/projects/${project}/runs/*/artifacts`, (route) =>
    route.fulfill({ json: artifacts.filter((a) => a.run_id === "previous") }),
  );
  await page.route(`**/api/projects/${project}/artifacts/*`, (route) =>
    route.fulfill({
      contentType: "image/png",
      body: Buffer.from(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jD1sAAAAASUVORK5CYII=",
        "base64",
      ),
    }),
  );
  return { task, artifacts, url: `/projects/${project}/tasks/${task.key}` };
}

test("task gallery preserves previous attempts, videos and downloads; attempt panel has one gallery", async ({
  page,
  request,
}) => {
  const { task, url } = await setup(page, request, "media-gallery");
  await page.goto(url);
  const gallery = page.getByRole("region", { name: "Artifacts", exact: true });
  await expect(gallery).toHaveCount(1);
  await expect(
    gallery.getByRole("img", { name: "Earlier screenshot" }),
  ).toBeVisible();
  await expect(
    gallery.getByRole("link", { name: "View Earlier screenshot" }),
  ).toHaveAttribute("href", "/api/projects/media-gallery/artifacts/image");
  await expect(gallery).toContainText("Previous attempt output");
  await expect(gallery.locator("video")).toHaveCount(2);
  for (const video of await gallery.locator("video").all()) {
    await expect(video).toHaveAttribute("controls", "");
    await expect(video).toHaveAttribute("preload", "metadata");
  }
  await expect(
    gallery.getByRole("link", { name: "Download walkthrough.webm" }),
  ).toHaveAttribute(
    "href",
    "/api/projects/media-gallery/artifacts/video?download=true",
  );
  await page.route(
    `**/api/projects/media-gallery/tasks/${task.id}/executions/worker:previous`,
    (route) =>
      route.fulfill({
        json: {
          id: "worker:previous",
          kind: "worker",
          purpose: "Implement task",
          status: "completed",
          owner: "worker",
          created_at: created,
          ended_at: created,
          run_id: "previous",
          integration_id: null,
          result_id: null,
          version: null,
        },
      }),
  );
  await page.route("**/api/projects/media-gallery/runs/previous", (route) =>
    route.fulfill({
      json: {
        id: "previous",
        status: "completed",
        model: "fixture",
        effort: "low",
        revision: 1,
      },
    }),
  );
  await page.goto(`${url}/runs/worker:previous`);
  await expect(gallery).toHaveCount(1);
  await expect(gallery).toContainText("Previous attempt output");
  await expect(gallery.locator("video")).toHaveCount(0);
});

test("gallery loading, empty and error states recover with retry and project activity", async ({
  page,
  request,
}) => {
  const { task, artifacts, url } = await setup(page, request, "media-states");
  let mode = "loading";
  let release: (() => void) | undefined;
  await page.route(
    `**/api/projects/media-states/tasks/${task.id}/artifacts`,
    async (route) => {
      if (mode === "loading")
        await new Promise<void>((resolve) => {
          release = resolve;
        });
      await route.fulfill(
        mode === "error"
          ? {
              status: 503,
              json: { error: { message: "Media temporarily unavailable" } },
            }
          : { json: mode === "gallery" ? artifacts : [] },
      );
    },
  );
  await page.goto(url);
  await expect(page.getByText("Loading artifacts…")).toBeVisible();
  mode = "empty";
  release!();
  await expect(page.getByText("No artifacts saved yet.")).toBeVisible();
  mode = "error";
  await page.evaluate(() =>
    window.dispatchEvent(
      new CustomEvent("flowfield:activity", {
        detail: { activity: [["media-states", "current"]] },
      }),
    ),
  );
  await expect(
    page.getByRole("alert").filter({ hasText: "Could not refresh artifacts" }),
  ).toContainText("Media temporarily unavailable");
  mode = "gallery";
  await page.getByRole("button", { name: "Retry artifacts" }).click();
  await expect(
    page.getByRole("img", { name: "Earlier screenshot" }),
  ).toBeVisible();
});

test("live is mounted on demand, sequential, read-only, retries without stale frames and stops on hide/close", async ({
  page,
  request,
}) => {
  const { url } = await setup(page, request, "media-live");
  const jpeg = await page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 2;
    canvas.height = 2;
    return canvas.toDataURL("image/jpeg").split(",")[1];
  });
  let active = true;
  let failed = false;
  let statusFailed = false;
  let statusDelay = 0;
  let readingStatus = false;
  let frames = 0;
  let statuses = 0;
  let pending = 0;
  let frameDelay = 100;
  let maxPending = 0;
  const starts: number[] = [];
  await page.addInitScript(() => {
    const original = URL.createObjectURL.bind(URL);
    const revoke = URL.revokeObjectURL.bind(URL);
    const urls = new Set<string>();
    const capture = { aborts: 0 };
    Object.assign(window, { mediaUrls: urls, mediaCapture: capture });
    const fetch = window.fetch.bind(window);
    window.fetch = (input, init) => {
      if (String(input).endsWith("/browser/frame"))
        init?.signal?.addEventListener("abort", () => capture.aborts++);
      return fetch(input, init);
    };
    URL.createObjectURL = (blob) => {
      const url = original(blob);
      urls.add(url);
      return url;
    };
    URL.revokeObjectURL = (url) => {
      urls.delete(url);
      revoke(url);
    };
  });
  await page.route(
    "**/api/projects/media-live/runs/current/browser",
    async (route) => {
      statuses++;
      readingStatus = true;
      await new Promise((resolve) => setTimeout(resolve, statusDelay));
      readingStatus = false;
      await route.fulfill(
        statusFailed
          ? {
              status: 503,
              json: { error: { message: "Browser connection lost" } },
            }
          : {
              json: {
                active,
                recording: false,
                url: "https://example.test",
                problem: null,
              },
            },
      );
    },
  );
  await page.route(
    "**/api/projects/media-live/runs/current/browser/frame",
    async (route) => {
      frames++;
      starts.push(Date.now());
      pending++;
      maxPending = Math.max(maxPending, pending);
      await new Promise((resolve) => setTimeout(resolve, frameDelay));
      pending--;
      await route.fulfill(
        failed
          ? { status: 409, body: "Unavailable" }
          : { contentType: "image/jpeg", body: Buffer.from(jpeg, "base64") },
      );
    },
  );
  await page.goto(url);
  const live = page.getByText("Live browser · read-only", { exact: true });
  await expect(live).toBeVisible();
  expect(statuses).toBe(0);
  expect(frames).toBe(0);
  await live.click();
  const frame = page.getByRole("img", {
    name: "Read-only worker browser preview",
  });
  await expect(frame).toBeVisible();
  await expect(
    page.getByText("Live · read-only", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("View only.", { exact: false })).toBeVisible();
  failed = true;
  await expect(
    page.getByText(/Browser disconnected. Retrying… Frame unavailable/),
  ).toBeVisible();
  await expect(frame).toHaveCount(0);
  failed = false;
  await expect(frame).toBeVisible();
  expect(maxPending).toBe(1);
  expect(
    starts.slice(1).every((start, index) => start - starts[index] >= 480),
  ).toBe(true);
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: true,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect(frame).toHaveCount(0);
  const hiddenFrames = frames;
  await page.waitForTimeout(700);
  expect(frames).toBe(hiddenFrames);
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: false,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect(frame).toBeVisible();
  statusFailed = true;
  await page.evaluate(() =>
    window.dispatchEvent(
      new CustomEvent("flowfield:activity", {
        detail: { activity: [["media-live", "current"]] },
      }),
    ),
  );
  await expect(
    page.getByText(/Browser disconnected. Retrying… Browser connection lost/),
  ).toBeVisible();
  await expect(frame).toHaveCount(0);
  statusFailed = false;
  statusDelay = 1000;
  await expect.poll(() => readingStatus).toBe(true);
  // Retained status data must not mount frames while a failed status is retrying.
  await expect(frame).toHaveCount(0);
  await expect(
    page.getByText(/Browser disconnected. Retrying… Browser connection lost/),
  ).toBeVisible();
  await expect(frame).toBeVisible();
  statusDelay = 0;
  active = false;
  await expect(page.getByText(/Browser inactive/)).toBeVisible();
  await expect(frame).toHaveCount(0);
  await live.click();
  const closedFrames = frames,
    closedStatuses = statuses;
  await page.waitForTimeout(700);
  expect(frames).toBe(closedFrames);
  expect(statuses).toBe(closedStatuses);
  expect(
    await page.evaluate(
      () => (window as unknown as { mediaUrls: Set<string> }).mediaUrls.size,
    ),
  ).toBe(0);
  active = true;
  await live.click();
  await expect(frame).toBeVisible();
  frameDelay = 1000;
  await expect.poll(() => pending).toBe(1);
  const aborts = await page.evaluate(
    () =>
      (window as unknown as { mediaCapture: { aborts: number } }).mediaCapture
        .aborts,
  );
  await live.click();
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (window as unknown as { mediaCapture: { aborts: number } })
            .mediaCapture.aborts,
      ),
    )
    .toBeGreaterThan(aborts);
  await expect(frame).toHaveCount(0);
  const stoppedFrames = frames;
  await page.waitForTimeout(700);
  expect(frames).toBe(stoppedFrames);
  expect(
    await page.evaluate(
      () => (window as unknown as { mediaUrls: Set<string> }).mediaUrls.size,
    ),
  ).toBe(0);
  frameDelay = 100;
  await live.click();
  await expect(frame).toBeVisible();
  await page.goto("/projects/media-live");
  const unmounted = frames;
  await page.waitForTimeout(700);
  expect(frames).toBe(unmounted);
});
