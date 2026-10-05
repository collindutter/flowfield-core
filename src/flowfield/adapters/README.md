# Harness adapters

Application records use Flowfield task and execution IDs. Harness session IDs,
transport, model capabilities, event translation and process termination stay at
this boundary; the service owns assignments, input delivery, approval and integration.

- `codex_connection.py` configures/probes the coordinator's native MCP connection.
  Connectivity does not prove a fresh coordinator followed project guidance, dispatch
  workers or wake an inactive conversation.
- `codex_agent.py` resolves the installed standalone bridge and native choices over ACP.
  Local workers use native coding tools and a revocable, run-bound Flowfield MCP endpoint.
- `local_execution.py` prepares explicitly adopted Local attempts. Git adapters own
  worktrees, candidate checks and delivery. The supervisor reserves work, freezes input,
  starts workers and reconciles recovery through the shared application operations.

The ACP path separates `GitWorkspace` (checkout and result capture),
`LocalHost`/`LocalAttempt` (explicit host environment and per-attempt scratch state),
and `LocalProcess` (owned POSIX process groups). New managed execution requires explicit
Local adoption. The native worker, tool inventory and forced language runtime are retired.
`historical_workspace.py` reads saved legacy locations/diffs; it cannot prepare or launch
work. Persisted settings and active runs are never silently converted to broader host access.

`LocalHost` preserves the supplied HOME, PATH and harness configuration, without a
tool inventory or mandatory language runtime. Its input is the intended launch
environment, not necessarily the service's activated virtual environment or an
interactive terminal's PATH. Do not persist its credential-bearing values. Each
attempt receives its own worktree, TMPDIR/TMP/TEMP, runtime directory and run identity;
project setup still owns dependencies, ports and test data. Shared mutable services
need distinct resources or serialized work. Native harness restrictions remain native.

ACP uses the same `LocalProcess` cleanup implementation as local subprocess tests.
The caller drains process streams and confirms its harness turn/tool lifecycle before
capturing a result. A process-group receipt does not prove detached descendants exited,
and a retained PID alone cannot establish process ownership after restart. Retain work
and uncertain execution until reconciliation; never kill processes by name. Worktrees
share Git metadata. Local capture allows commits descending from the original baseline
only in the detached checkout, then creates the reviewed result from that original parent.
A changed checkout binding, branch or unrelated ancestry fails explicitly. Historical commits remain unchanged. Native tools must not modify shared branches/refs.

Managed answers continue through service-owned attempts, preserving assignment and answer
bindings. Saved input, reservation and execution remain distinct facts. Worker continuation
uses preserved workspaces and deliberate context; it does not promise a persistent harness
session. The browser reads application activity, never provider messages directly.

Mid-run steering, embedded coordinator sessions and additional production harnesses
remain future work. Deterministic adapters test application behavior without model calls.
The installed service requires no Node runtime.

Local setup, validation and inspection use the same host/tooling model. Saved inspection
launchers inherit the human terminal environment and contain only copy-specific paths,
never a serialized credential-bearing service environment. Commands are trusted project
configuration, with bounded output and owned group cleanup; they must not daemonize.
Native cleanup uncertainty stays distinct from group exit. Recovery never signals a new
Local attempt from a persisted PID; unconfirmed native ownership keeps capacity reserved.

Permission projections retain at most 32 public tool details per turn, each bounded to
16,000 characters. Codex adds known command/cwd/permission facts; unrelated raw inputs,
metadata and reasoning stay excluded. ACP context occupancy is not billable token usage;
unsupported input/output totals remain unknown. Catalog discovery is shared by concurrent
callers, bounded to 64 model selections and 120 seconds, and starts no model turn.
