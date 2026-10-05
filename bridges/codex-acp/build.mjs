// Build a reviewed source patch; never modify a user's installed bridge.
import { createHash } from "node:crypto";
import { cp, mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";
import { execFileSync } from "node:child_process";

const root = dirname(fileURLToPath(import.meta.url));
const revision = "68d7d2d5ddfc0ed5746f9f6130892dda685e65dd";
const archiveHash =
  "3778cf2b1bbaa656d684bfa6b177ac5d5fd9202aee48623b8200e3b216ade0f6";
const work = join(root, ".work");
const source = join(work, "upstream");
await mkdir(source, { recursive: true });
const archive = process.argv[2]
  ? await readFile(resolve(process.argv[2]))
  : Buffer.from(
      await (
        await fetch(
          `https://api.github.com/repos/agentclientprotocol/codex-acp/tarball/${revision}`,
        )
      ).arrayBuffer(),
    );
if (createHash("sha256").update(archive).digest("hex") !== archiveHash) {
  throw new Error(
    "Upstream source checksum mismatch; review the dependency before rebuilding",
  );
}
const archivePath = join(work, "upstream.tar.gz");
await writeFile(archivePath, archive);
execFileSync("tar", [
  "-xzf",
  archivePath,
  "-C",
  source,
  "--strip-components=1",
]);
let index = await readFile(join(source, "src/index.ts"), "utf8");
if (
  createHash("sha256").update(index).digest("hex") !==
  "c9cddb3bd3bb653079785debde08991d521cf68e8eded15586b05681280d4693"
) {
  throw new Error("Unexpected bridge entry point");
}
// All modifications to the pinned upstream are here. Its native coding/tool and
// permission implementation stays upstream-owned. Generated output is untracked.
index = index.replace(
  "#!/usr/bin/env node",
  "#!/usr/bin/env node\n// Modified by Flowfield: dedicated-session cleanup capability and request fence.",
);
index = index.replace(
  'import * as acp from "@agentclientprotocol/sdk";',
  'import * as acp from "@agentclientprotocol/sdk";\nimport {Cleanup, capability} from "./flowfield-cleanup.mjs";',
);
index = index.replace(
  "console.log(`${packageJson.name} ${packageJson.version}`);",
  'console.log("flowfield-codex-acp 2.1.1-flowfield.1");',
);
index = index.replace(
  "    const acpJsonStream = createJsonStream(process.stdin, process.stdout);",
  `
    const nativeOwner = codexProcessState.connection;
    const cleanup = new Cleanup(
        (method: string, params: object) => {
            if (codexProcessState.connection !== nativeOwner) throw new Error("Native owner changed");
            return nativeOwner.connection.sendRequest(method, params);
        },
        (sessionId: string) => getAgent().cancel({sessionId}),
    );
    const acpJsonStream = createJsonStream(process.stdin, process.stdout);`,
);
// Fence every request which can create/change work, including provider replacement
// and extension routes. Cancel and close can only reduce work and remain available.
index = index.replaceAll(
  /\(ctx\) => getAgent\(\)\.([^\n]+)/g,
  (match, call) => {
    if (call.startsWith("cancel(") || call.startsWith("closeSession("))
      return match;
    return `(ctx) => cleanup.run(() => getAgent().${call.slice(0, -1)}))`;
  },
);
index = index.replace(
  "cleanup.run(() => getAgent().initialize(ctx.params))",
  `cleanup.run(async () => {
            const result = await getAgent().initialize(ctx.params);
            return {...result, agentInfo: {...result.agentInfo, name: "flowfield-codex-acp", version: "2.1.1-flowfield.1"}, agentCapabilities: {...result.agentCapabilities,
                _meta: {...result.agentCapabilities?._meta, "flowfield.cleanup": capability}}};
        })`,
);
for (const method of [
  "newSession",
  "forkSession",
  "loadSession",
  "resumeSession",
]) {
  const bound = ["loadSession", "resumeSession"].includes(method)
    ? ", ctx.params.sessionId"
    : "";
  index = index.replace(
    `cleanup.run(() => getAgent().${method}(ctx.params))`,
    `cleanup.attach(() => getAgent().${method}(ctx.params)${bound})`,
  );
}
index = index.replace(
  "        .connect(acpJsonStream);",
  `
        .onRequest("_flowfield/quiesce", z.object({sessionId: z.string().min(1).max(500)}).strict(),
            (ctx) => cleanup.stop(ctx.params.sessionId))
        .connect(acpJsonStream);`,
);
await writeFile(join(source, "src/index.ts"), index);
const nativeFile = join(source, "src/CodexAppServerClient.ts");
let native = await readFile(nativeFile, "utf8");
native =
  "// Modified by Flowfield: observe native lifecycle events before bridge translation.\n" +
  native;
native = native.replace(
  "    constructor(connection: MessageConnection) {",
  "    public flowfieldObserve?: (message: unknown) => void;\n\n    constructor(connection: MessageConnection) {",
);
native = native.replace(
  "            const serverNotification = data as ServerNotification;",
  "            this.flowfieldObserve?.(data);\n            const serverNotification = data as ServerNotification;",
);
await writeFile(nativeFile, native);
index = index.replace(
  "        const appServerClient = new CodexAppServerClient(codexProcessState.connection.connection);",
  "        const appServerClient = new CodexAppServerClient(codexProcessState.connection.connection);\n        appServerClient.flowfieldObserve = (message) => cleanup.observe(message);",
);
await writeFile(join(source, "src/index.ts"), index);
await cp(join(root, "cleanup.mjs"), join(source, "src/flowfield-cleanup.mjs"));
await cp(
  join(root, "cleanup.d.mts"),
  join(source, "src/flowfield-cleanup.d.mts"),
);
// Use upstream's immutable npm lock, without lifecycle scripts. This is a build
// dependency, not a new package manager for Flowfield's web/docs workspaces.
execFileSync("npm", ["ci", "--ignore-scripts", "--no-audit", "--no-fund"], {
  cwd: source,
  stdio: "inherit",
});
execFileSync("npm", ["run", "typecheck"], { cwd: source, stdio: "inherit" });
execFileSync(process.execPath, ["build.mjs"], {
  cwd: source,
  stdio: "inherit",
});
console.log(join(source, "dist/index.js"));
