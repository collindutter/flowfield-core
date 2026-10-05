// Flowfield's versioned extension for a dedicated, service-owned Codex process.
// No process scanning, shell commands, history deletion or policy substitution.
export const capability = Object.freeze({
  version: 1,
  method: "_flowfield/quiesce",
  scope: "native-turns-and-terminals",
});

export class Cleanup {
  constructor(
    rpc,
    cancel,
    { timeoutMs = 10000, maxThreads = 64, maxTerminals = 256 } = {},
  ) {
    this.rpc = rpc;
    this.cancel = cancel;
    this.timeoutMs = timeoutMs;
    this.maxThreads = maxThreads;
    this.maxTerminals = maxTerminals;
    this.pending = new Set();
    this.sessions = new Set();
    this.sealed = false;
    this.result = null;
    this.sessionId = null;
    this.claimed = false;
    this.turns = new Map();
    this.epoch = 0;
    this.invalidEvents = false;
  }

  observe(message) {
    if (
      ![
        "turn/started",
        "turn/completed",
        "thread/started",
        "thread/closed",
      ].includes(message?.method)
    )
      return;
    this.epoch++;
    if (!message.method.startsWith("turn/")) return;
    const { threadId, turn } = message.params ?? {};
    if (
      typeof threadId !== "string" ||
      !threadId ||
      threadId.length > 500 ||
      typeof turn?.id !== "string" ||
      !turn.id ||
      turn.id.length > 500
    ) {
      this.invalidEvents = true;
      return;
    }
    if (message.method === "turn/started") {
      if (this.turns.size >= this.maxThreads && !this.turns.has(threadId)) {
        this.invalidEvents = true;
        return;
      }
      this.turns.set(threadId, turn.id);
    } else if (this.turns.get(threadId) === turn.id)
      this.turns.delete(threadId);
  }

  // Every work-creating ACP entry point passes through this fence. Register the
  // promise synchronously: cleanup cannot race a session startup already accepted.
  run(operation) {
    if (this.sealed) throw new Error("This managed bridge has been stopped");
    const task = Promise.resolve().then(operation);
    this.pending.add(task);
    task.then(
      () => this.pending.delete(task),
      () => this.pending.delete(task),
    );
    return task;
  }

  bind(session) {
    if (!session || typeof session.sessionId !== "string")
      throw new Error("Missing session");
    this.sessions.add(session.sessionId);
    return session;
  }

  attach(operation, sessionId = null) {
    return this.run(async () => {
      if (this.claimed) throw new Error("Managed bridges own one root session");
      this.claimed = true;
      const result = await operation();
      this.bind(sessionId === null ? result : { sessionId });
      return result;
    });
  }

  stop(sessionId) {
    if (!this.sessions.has(sessionId))
      throw new Error("Unknown managed session");
    if (this.result) {
      if (sessionId !== this.sessionId)
        throw new Error("Cleanup belongs to another session");
      return this.result;
    }
    this.sessionId = sessionId;
    this.sealed = true;
    this.result = this.drain(sessionId);
    return this.result;
  }

  async drain(sessionId) {
    const deadline = Date.now() + this.timeoutMs;
    const checked = new Set();
    let stopped = 0;
    const bounded = async (operation) => {
      const remaining = deadline - Date.now();
      if (remaining <= 0) throw new Error("timeout");
      let timer;
      try {
        return await Promise.race([
          operation(),
          new Promise((_, reject) => {
            timer = setTimeout(() => reject(new Error("timeout")), remaining);
          }),
        ]);
      } finally {
        clearTimeout(timer);
      }
    };
    const call = (method, params) => bounded(() => this.rpc(method, params));
    const page = async (method, params, limit) => {
      const items = [];
      const cursors = new Set();
      let cursor = null;
      do {
        const result = await call(method, {
          ...params,
          cursor,
          ...(method === "thread/loaded/list"
            ? { limit: Math.min(limit, 100) }
            : {}),
        });
        if (
          !Array.isArray(result?.data) ||
          !(result.nextCursor === null || typeof result.nextCursor === "string")
        ) {
          throw new Error("invalid_response");
        }
        items.push(...result.data);
        if (items.length > limit || cursors.size >= 16)
          throw new Error("resource_limit");
        cursor = result.nextCursor;
        if (cursor !== null) {
          if (cursors.has(cursor)) throw new Error("invalid_response");
          cursors.add(cursor);
        }
      } while (cursor !== null);
      return items;
    };
    const receipt = (status, reason = null) => ({
      ...capability,
      sessionId,
      status,
      reason,
      checkedThreads: checked.size,
      stoppedTerminals: stopped,
    });
    try {
      // ACP cancellation is a request, not our native-stop evidence.
      await bounded(() => this.cancel(sessionId));
      await bounded(() => Promise.allSettled([...this.pending]));
      let previous = null;
      for (let pass = 0; pass < 100; pass++) {
        if (this.invalidEvents) throw new Error("invalid_response");
        const epoch = this.epoch;
        const ids = await page("thread/loaded/list", {}, this.maxThreads);
        if (
          ids.some((id) => typeof id !== "string" || !id || id.length > 500) ||
          new Set(ids).size !== ids.length
        ) {
          throw new Error("invalid_response");
        }
        // A disappeared root can hide native cleanup failure. Never infer stopped
        // work merely because a thread was removed from the loaded registry.
        if (
          !ids.includes(sessionId) ||
          [...checked].some((id) => !ids.includes(id))
        ) {
          throw new Error("thread_disappeared");
        }
        let idle = true;
        for (const threadId of ids) {
          checked.add(threadId);
          if (checked.size > this.maxThreads) throw new Error("resource_limit");
          const metadata = await call("thread/read", {
            threadId,
            includeTurns: false,
          });
          if (
            metadata?.thread?.id !== threadId ||
            typeof metadata.thread.ephemeral !== "boolean"
          )
            throw new Error("invalid_response");
          // Ephemeral native threads cannot own goals; native metadata supplies
          // that distinction, not error-string matching or a guessed fallback.
          const goal = metadata.thread.ephemeral
            ? { goal: null }
            : await call("thread/goal/get", { threadId });
          if (
            !goal ||
            !(
              goal.goal === null ||
              [
                "active",
                "paused",
                "blocked",
                "usageLimited",
                "budgetLimited",
                "complete",
              ].includes(goal.goal?.status)
            )
          )
            throw new Error("invalid_response");
          if (goal.goal?.status === "active") {
            await call("thread/goal/set", { threadId, status: "paused" });
            idle = false;
          }
          const turnId = this.turns.get(threadId);
          if (turnId) {
            await call("turn/interrupt", { threadId, turnId });
            idle = false;
          }
          const terminals = await page(
            "thread/backgroundTerminals/list",
            { threadId },
            this.maxTerminals,
          );
          for (const terminal of terminals) {
            if (typeof terminal.processId !== "string" || !terminal.processId)
              throw new Error("invalid_response");
            if (++stopped > this.maxTerminals)
              throw new Error("resource_limit");
            const result = await call("thread/backgroundTerminals/terminate", {
              threadId,
              processId: terminal.processId,
            });
            if (result?.terminated !== true)
              throw new Error("termination_unconfirmed");
            idle = false;
          }
          const state = await call("thread/read", {
            threadId,
            includeTurns: false,
          });
          if (
            state?.thread?.id !== threadId ||
            !["active", "idle"].includes(state.thread.status?.type)
          ) {
            throw new Error("native_state_unconfirmed");
          }
          if (state.thread.status.type !== "idle") idle = false;
        }
        if (this.turns.size || epoch !== this.epoch) idle = false;
        const signature = JSON.stringify([[...ids].sort(), this.epoch]);
        if (idle && previous === signature) return receipt("confirmed");
        previous = idle ? signature : null;
        await bounded(() => new Promise((resolve) => setTimeout(resolve, 50)));
      }
      return receipt("uncertain", "resource_limit");
    } catch (error) {
      // Do not return native diagnostics, commands or credentials in receipts.
      const reasons = [
        "timeout",
        "invalid_response",
        "resource_limit",
        "thread_disappeared",
        "termination_unconfirmed",
        "native_state_unconfirmed",
      ];
      return receipt(
        "uncertain",
        reasons.includes(error?.message)
          ? error.message
          : "native_request_failed",
      );
    }
  }
}
