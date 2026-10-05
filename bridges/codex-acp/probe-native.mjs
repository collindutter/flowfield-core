// Explicit model-free probe of the installed native command lifecycle.
// Never included in normal tests/CI; HOME and Codex state are disposable.
import { spawn } from "node:child_process";
import { mkdtemp, mkdir, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createInterface } from "node:readline";
import { Cleanup } from "./cleanup.mjs";

const binary = process.argv[2];
if (!binary)
  throw new Error("Usage: node probe-native.mjs /absolute/path/to/codex");
const scratch = await mkdtemp(join(tmpdir(), "flowfield-native-cleanup-"));
const home = join(scratch, "home");
const cwd = join(scratch, "project");
await mkdir(home);
await mkdir(cwd);
const processOwner = spawn(resolve(binary), ["app-server"], {
  cwd,
  env: {
    HOME: home,
    CODEX_HOME: home,
    PATH: "/usr/bin:/bin:/usr/sbin:/sbin",
    NO_COLOR: "1",
  },
  stdio: ["pipe", "pipe", "pipe"],
  detached: true,
});
processOwner.stderr.resume();
let sequence = 0;
const pending = new Map();
processOwner.on("error", (error) => {
  for (const waiter of pending.values()) waiter.reject(error);
  pending.clear();
});
processOwner.on("close", () => {
  for (const waiter of pending.values())
    waiter.reject(new Error("Native process exited before replying"));
  pending.clear();
});
let started = false;
const owner = new Cleanup(rpc, async () => {}, { timeoutMs: 10000 });
const read = createInterface({ input: processOwner.stdout });
read.on("line", (line) => {
  const message = JSON.parse(line);
  owner.observe(message);
  if (
    message.method === "item/started" &&
    message.params?.item?.type === "commandExecution"
  )
    started = true;
  const waiter = pending.get(message.id);
  if (waiter) {
    pending.delete(message.id);
    if (message.error)
      console.error(waiter.method, JSON.stringify(message.error));
    message.error
      ? waiter.reject(new Error("native_request_failed"))
      : waiter.resolve(message.result);
  }
});
function rpc(method, params) {
  return new Promise((resolve, reject) => {
    const id = ++sequence;
    pending.set(id, { resolve, reject, method });
    processOwner.stdin.write(JSON.stringify({ id, method, params }) + "\n");
  });
}
const watchdog = setTimeout(() => processOwner.kill("SIGKILL"), 30000);
try {
  await rpc("initialize", {
    clientInfo: { name: "flowfield_cleanup_probe", version: "1" },
    capabilities: { experimentalApi: true },
  });
  processOwner.stdin.write(
    JSON.stringify({ method: "initialized", params: {} }) + "\n",
  );
  const { thread } = await rpc("thread/start", {
    cwd,
    ephemeral: process.argv.includes("--ephemeral"),
    config: {
      mcp_servers: {},
      "features.plugins": false,
      "features.hooks": false,
      "features.remote_plugin": false,
      "features.apps": false,
    },
  });
  await rpc("thread/shellCommand", {
    threadId: thread.id,
    command: "echo $$ > owned-shell.pid; exec sleep 20",
    timeoutMs: 25000,
  });
  const deadline = Date.now() + 5000;
  while (!started && Date.now() < deadline)
    await new Promise((resolve) => setTimeout(resolve, 20));
  if (!started) throw new Error("Native command did not start");
  let ownedPid;
  while (!ownedPid && Date.now() < deadline) {
    try {
      ownedPid = Number(await readFile(join(cwd, "owned-shell.pid"), "utf8"));
    } catch {}
    if (!ownedPid) await new Promise((resolve) => setTimeout(resolve, 20));
  }
  if (!Number.isInteger(ownedPid) || ownedPid <= 1)
    throw new Error("Missing owned command identity");
  owner.bind({ sessionId: thread.id });
  const receipt = await owner.stop(thread.id);
  let commandExited = false;
  try {
    process.kill(ownedPid, 0);
  } catch (error) {
    commandExited = error.code === "ESRCH";
  }
  console.log(
    JSON.stringify(
      { nativeCommandStarted: started, commandExited, receipt },
      null,
      2,
    ),
  );
  if (receipt.status !== "confirmed" || !commandExited) process.exitCode = 1;
} finally {
  clearTimeout(watchdog);
  processOwner.stdin.end();
  await new Promise((resolve) => {
    if (processOwner.exitCode !== null || processOwner.signalCode !== null)
      return resolve();
    const timer = setTimeout(() => {
      try {
        process.kill(-processOwner.pid, "SIGKILL");
      } catch {}
    }, 3000);
    processOwner.once("close", () => {
      clearTimeout(timer);
      resolve();
    });
  });
  read.close();
  await rm(scratch, { recursive: true, force: true });
}
