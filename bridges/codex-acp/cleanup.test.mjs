import assert from "node:assert/strict";
import { test } from "node:test";
import { Cleanup } from "./cleanup.mjs";

function fixture(change = () => {}, options = {}) {
  const state = {
    ids: ["root", "child"],
    active: new Set(["root", "child"]),
    terminals: new Map([["child", ["owned-command"]]]),
    calls: [],
  };
  const rpc = async (method, params) => {
    state.calls.push([method, params]);
    const override = await change(method, params, state);
    if (override !== undefined) return override;
    switch (method) {
      case "thread/loaded/list":
        return { data: state.ids, nextCursor: null };
      case "thread/goal/get":
        return { goal: null };
      case "turn/interrupt":
        state.active.delete(params.threadId);
        owner.observe({
          method: "turn/completed",
          params: { threadId: params.threadId, turn: { id: "turn" } },
        });
        return {};
      case "thread/backgroundTerminals/list":
        return {
          data: (state.terminals.get(params.threadId) ?? []).map(
            (processId) => ({ processId }),
          ),
          nextCursor: null,
        };
      case "thread/backgroundTerminals/terminate":
        state.terminals.set(params.threadId, []);
        return { terminated: true };
      case "thread/read":
        return {
          thread: {
            id: params.threadId,
            ephemeral: false,
            status: {
              type: state.active.has(params.threadId) ? "active" : "idle",
            },
          },
        };
      default:
        throw new Error("unexpected " + method);
    }
  };
  const owner = new Cleanup(rpc, async () => {}, {
    timeoutMs: 1000,
    ...options,
  });
  owner.bind({ sessionId: "root" });
  for (const threadId of state.active)
    owner.observe({
      method: "turn/started",
      params: { threadId, turn: { id: "turn" } },
    });
  return { owner, state };
}

test("drains root, child and native terminals, fences work and caches receipt", async () => {
  const { owner, state } = fixture();
  const pending = owner.stop("root");
  assert.throws(() => owner.run(() => {}), /stopped/);
  const result = await pending;
  assert.equal(result.status, "confirmed");
  assert.equal(result.checkedThreads, 2);
  assert.equal(result.stoppedTerminals, 1);
  assert.equal(await owner.stop("root"), result);
  assert.throws(() => owner.stop("foreign"), /Unknown/);
  assert.deepEqual([...state.active], []);
  assert(
    state.calls.filter(([method]) => method === "thread/loaded/list").length >=
      3,
  );
});

for (const [name, change, reason] of [
  [
    "unacknowledged terminal stop",
    (m) => (m.endsWith("/terminate") ? { terminated: false } : undefined),
    "termination_unconfirmed",
  ],
  [
    "unsupported native API",
    (m) => {
      if (m === "thread/loaded/list") throw new Error("SECRET diagnostics");
    },
    "native_request_failed",
  ],
  [
    "repeated pagination cursor",
    (m) =>
      m === "thread/loaded/list"
        ? { data: ["root"], nextCursor: "repeat" }
        : undefined,
    "invalid_response",
  ],
  [
    "unknown status",
    (m) =>
      m === "thread/read"
        ? {
            thread: {
              id: "root",
              ephemeral: false,
              status: { type: "systemError" },
            },
          }
        : undefined,
    "native_state_unconfirmed",
  ],
  [
    "disappeared root",
    (m) =>
      m === "thread/loaded/list" ? { data: [], nextCursor: null } : undefined,
    "thread_disappeared",
  ],
]) {
  test(name + " retains uncertainty", async () => {
    const { owner } = fixture(change);
    const result = await owner.stop("root");
    assert.equal(result.status, "uncertain");
    assert.equal(result.reason, reason);
    assert(!JSON.stringify(result).includes("SECRET"));
  });
}

test("timeout is bounded and cannot reopen the process", async () => {
  const { owner } = fixture(() => new Promise(() => {}), { timeoutMs: 25 });
  assert.equal((await owner.stop("root")).reason, "timeout");
  assert.throws(() => owner.run(() => {}), /stopped/);
});

test("cleanup waits for accepted ACP operations; separate owners keep running", async () => {
  const first = fixture();
  const second = fixture();
  let release;
  first.owner.run(
    () =>
      new Promise((resolve) => {
        release = resolve;
      }),
  );
  const pending = first.owner.stop("root");
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(first.state.calls.length, 0);
  assert.equal(await second.owner.run(() => 42), 42);
  release();
  assert.equal((await pending).status, "confirmed");
});

test("a native goal is paused before confirming a quiet thread", async () => {
  let active = true;
  const { owner } = fixture((method) => {
    if (method === "thread/goal/get")
      return { goal: { status: active ? "active" : "paused" } };
    if (method === "thread/goal/set") {
      active = false;
      return {};
    }
  });
  assert.equal((await owner.stop("root")).status, "confirmed");
  assert.equal(active, false);
});

test("one root session per process, including overlapping starts", async () => {
  const owner = new Cleanup(
    async () => {},
    async () => {},
  );
  let release;
  const first = owner.attach(
    () =>
      new Promise((resolve) => {
        release = resolve;
      }),
  );
  await assert.rejects(
    owner.attach(async () => ({ sessionId: "other" })),
    /one root/,
  );
  release({ sessionId: "root" });
  await first;
});

test("ephemeral threads do not call unsupported goal APIs", async () => {
  const { owner } = fixture((method, params, state) => {
    if (method === "thread/goal/get") throw new Error("unsupported");
    if (method === "thread/read")
      return {
        thread: {
          id: params.threadId,
          ephemeral: true,
          status: {
            type: state.active.has(params.threadId) ? "active" : "idle",
          },
        },
      };
  });
  assert.equal((await owner.stop("root")).status, "confirmed");
});

test("unknown goal state cannot be treated as inactive", async () => {
  const { owner } = fixture((method) =>
    method === "thread/goal/get"
      ? { goal: { status: "new-native-state" } }
      : undefined,
  );
  assert.equal((await owner.stop("root")).reason, "invalid_response");
});

test("thread enumeration stops at the resource limit", async () => {
  const { owner } = fixture(
    (method) =>
      method === "thread/loaded/list"
        ? { data: ["root", "child", "excess"], nextCursor: null }
        : undefined,
    { maxThreads: 2 },
  );
  assert.equal((await owner.stop("root")).reason, "resource_limit");
});

test("a terminal acknowledgment cannot hide a remaining terminal", async () => {
  const { owner } = fixture(
    (method) =>
      method.endsWith("/terminate") ? { terminated: true } : undefined,
    { maxTerminals: 2 },
  );
  assert.equal((await owner.stop("root")).reason, "resource_limit");
});

test("native completion is required even when thread status says idle", async () => {
  const { owner } = fixture(
    (method, params) => {
      if (method === "turn/interrupt") return {};
      if (method === "thread/read")
        return {
          thread: {
            id: params.threadId,
            ephemeral: false,
            status: { type: "idle" },
          },
        };
    },
    { timeoutMs: 150 },
  );
  assert.equal((await owner.stop("root")).reason, "timeout");
});

test("a late native turn resets quiet observations and is stopped", async () => {
  let listings = 0;
  const { owner, state } = fixture((method, params, state) => {
    if (method === "thread/loaded/list" && ++listings === 3) {
      state.active.add("child");
      owner.observe({
        method: "turn/started",
        params: { threadId: "child", turn: { id: "turn" } },
      });
    }
  });
  assert.equal((await owner.stop("root")).status, "confirmed");
  assert(listings >= 5);
  assert.equal(
    state.calls.filter(
      ([method, params]) =>
        method === "turn/interrupt" && params.threadId === "child",
    ).length,
    2,
  );
});
