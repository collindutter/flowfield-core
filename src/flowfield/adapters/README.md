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

Managed answers continue through service-owned attempts, preserving assignment and answer
bindings. Saved input, reservation and execution remain distinct facts. Worker continuation
uses preserved workspaces and deliberate context; it does not promise a persistent harness
session. The browser reads application activity, never provider messages directly.

Mid-run steering, embedded coordinator sessions, ACP and additional production harnesses
remain future work. Deterministic adapters test application behavior without model calls.
The installed service requires no Node runtime.
