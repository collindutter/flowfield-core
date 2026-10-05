# Harness adapters

Application records use Flowfield task and execution IDs. Harness session IDs,
transport, model capabilities, event translation and process termination stay at
this boundary; the service owns assignments, input delivery, approval and integration.

- `codex_connection.py` configures/probes the coordinator's native MCP connection.
  Connectivity does not prove a fresh coordinator followed project guidance, dispatch
  workers or wake an inactive conversation.
- `codex_worker.py` owns managed Codex App Server sessions, scoped tools, explicit
  model/effort, usage and verified interruption. `codex_activity.py` translates allowed
  public events into bounded application activity; raw/private diagnostics are excluded.
- `local_environment.py` prepares managed execution environments; Git adapters own
  worktrees, candidate checks and delivery. The supervisor reserves work, freezes input,
  starts workers and reconciles recovery through the shared application operations.

The internal ACP foundation separates `GitWorkspace` (checkout and result capture),
`LocalHost`/`LocalAttempt` (explicit host environment and per-attempt scratch state),
and `LocalProcess` (owned POSIX process groups). The existing native worker still uses
its original runtime/settings through `local_environment.py`; no persisted settings
or active runs are automatically converted to broader host access.

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
share Git metadata, and result capture currently requires the original checkout HEAD.
Native worker commit support must be resolved before relaxing that result invariant.

Managed answers continue through service-owned attempts, preserving assignment and answer
bindings. Saved input, reservation and execution remain distinct facts. Worker continuation
uses preserved workspaces and deliberate context; it does not promise a persistent harness
session. The browser reads application activity, never provider messages directly.

Mid-run steering, embedded coordinator sessions, ACP and additional production harnesses
remain future work. Deterministic adapters test application behavior without model calls.
The installed service requires no Node runtime.
