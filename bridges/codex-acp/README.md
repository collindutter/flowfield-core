# Managed Codex cleanup

This directory maintains Flowfield's small cleanup extension to
[`@agentclientprotocol/codex-acp`](https://github.com/agentclientprotocol/codex-acp).
The upstream bridge still owns coding tools, permissions, models and ACP translation.

## Build and check

```sh
node --test bridges/codex-acp/cleanup.test.mjs
node bridges/codex-acp/build.mjs
```

The builder downloads upstream commit `68d7d2d5ddfc0ed5746f9f6130892dda685e65dd`
(2.1.1), checks its archive and entry-point hashes, applies the reviewed changes in
`build.mjs`, typechecks the patched bridge, and builds with upstream's locked dependencies. An optional argument
supplies an already-downloaded archive. It never changes an installed bridge.
Generated source, dependencies and output stay under the ignored `.work/` directory.
Upstream's Apache-2.0 license remains with the generated source. The ACP identity is
`flowfield-codex-acp`, version `2.1.1-flowfield.1`.

The generated entry point is `.work/upstream/dist/index.js`. It uses Node and the
locked Codex dependency (0.159.1); `CODEX_PATH` can select an explicitly installed
native binary. This source build does not alter Flowfield's installed runtime.

## Standalone packages

Build the source above, then run `node bridges/codex-acp/package.mjs` with Bun 1.3.11
on PATH (or set `FLOWFIELD_BUILD_BUN` to that compiler's absolute path). The compiler
version and bridge identity live in `src/flowfield/adapters/codex_bridge.json`.
This follows upstream's [Bun standalone build](https://bun.sh/docs/bundler/executables).
Node, Bun and zip are build tools; the resulting executable includes its JS runtime.
It invokes the host's Codex, honoring `CODEX_PATH`, rather than shipping another Codex
installation. Native Codex login/configuration remain separate.

Packages for macOS/Linux arm64/x64 are built and tested on matching CI runners.
`.work/packages/` contains a ZIP and SHA-256 sidecar for each build. Archives include
the executable, exact manifest/file checksums, Apache license and dependency/runtime
notices. Project `.env`, bunfig, tsconfig and package.json are not auto-loaded by the
standalone runtime. The managed launcher removes bridge wire logging because it can
contain scoped MCP credentials; it retains native configuration and host environment.

```sh
uv run scripts/check_codex_acp.py /path/to/bundle.zip --bundle --cleanup
flowfield harness install codex --bundle /path/to/bundle.zip --sha256 CHECKSUM
flowfield harness status codex --json
```

The bundle probe installs into disposable state and runs without Node/Bun on PATH.
CI checks completion, cancellation, background termination and refused cleanup against
a fake native peer. It does not invoke models. Installation itself starts no executable.
The installer uses immutable generation directories and an atomic selection pointer;
failed downloads, wrong platforms, checksum failures and malformed archives preserve
the previous installation. Old executable paths remain available to existing attempts.
Normal installation selects assets from the installed Flowfield version's GitHub release,
never a moving latest tag. Local bundles require an explicit trusted SHA-256.

The release workflow attaches those same tested archives after its normal publishing
gates. Manual rehearsal and main-branch CI retain artifacts without publishing a release.
Installing the bridge does not enable queues or select worker models.

An explicit **model-free** native probe launches only a disposable thread and sleep
command, verifies that command exits, and removes its isolated HOME/CODEX_HOME:

```sh
node bridges/codex-acp/probe-native.mjs /absolute/path/to/codex
node bridges/codex-acp/probe-native.mjs /absolute/path/to/codex --ephemeral
```

The shared-client probe exercises the built bridge against a fake native server:

```sh
uv run scripts/check_codex_acp.py bridges/codex-acp/.work/upstream/dist/index.js --cleanup
uv run scripts/check_codex_acp.py bridges/codex-acp/.work/upstream/dist/index.js --cleanup --scenario cancel
uv run scripts/check_codex_acp.py bridges/codex-acp/.work/upstream/dist/index.js --cleanup --scenario background
uv run scripts/check_codex_acp.py bridges/codex-acp/.work/upstream/dist/index.js --cleanup --scenario cleanup-refused
uv run scripts/check_codex_acp.py bridges/codex-acp/.work/upstream/dist/index.js --cleanup --scenario resume
```

The resume scenario closes the first process, resumes the native thread in a second,
and checks renewed scoped MCP configuration and retained fake-native history without
replaying earlier messages. It does not prove live-model context or compaction behavior.

Ordinary tests never invoke native Codex or models. Native foreground-command exit was
measured on macOS with Codex 0.159.1 and 0.159.2. Background-terminal termination and
delegated-thread scenarios have deterministic protocol tests; actual agent behavior
needs a separately invoked model trial.

## Contract

`agentCapabilities._meta["flowfield.cleanup"]` advertises version 1, method
`_flowfield/quiesce`, and scope `native-turns-and-terminals`. The request contains the
owned `sessionId`. The receipt repeats those fields and returns `status`, `reason`,
`checkedThreads`, and `stoppedTerminals`. Only an exact, same-session `confirmed`
receipt with a nonempty checked scope is positive evidence.

Use one root ACP session per dedicated native app-server process. Cleanup:

1. Permanently rejects new ACP work and waits for accepted operations to settle.
2. Enumerates that process's loaded native threads, including delegated work.
3. Pauses active native goals, interrupts observed native turns, terminates native
   background-terminal handles, and checks that threads are idle and terminals absent.
4. Requires two quiet observations with unchanged native-event generation and thread
   membership. Keeps the result for duplicate cleanup calls; never reopens the process.

Limits are 10 seconds, 64 threads, 256 termination attempts, 16 pages per query and
100 observation passes. Timeouts, unsupported APIs, malformed replies, disappearing
threads, native-owner replacement and unconfirmed termination return uncertainty.
Raw native diagnostics, command text and credentials never enter the receipt.
Normal session close/transport shutdown happens afterward and remains separately
reported. No history is deleted or archived to manufacture cleanup confirmation.

This covers **native turns and tracked terminals**. It does not promise containment
of arbitrary daemons, external MCP services, or shared local resources. Process-group
exit alone does not expand that scope. A service restart cannot replay this receipt
against a different native owner; preserved uncertain attempts require reconciliation.

## Upgrades and removal

Review native lifecycle API semantics and upstream source before changing the pinned
revision/hashes. Rebuild, run deterministic tests, exercise the released-bridge peer
probe, and repeat native command-exit probes against the exact dependency. Keep
dependency upgrades separate from protocol-version changes. Publish no unverified
cleanup claim. Replace this extension when upstream provides an equivalent negotiated
capability; keep the application cleanup callback and uncertainty behavior.
