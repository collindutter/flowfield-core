# Changelog

Reviewed release notes describe changes by user impact. See CONTRIBUTING.md for versioning
and release preparation. A version heading becomes a release when its package is published.

## 0.2.0

Plan, run and review work together in Flowfield with the embedded Coordinator.

- Keep a persistent Coordinator conversation beside the board and selected task. Resume
  native conversation history, attach files, select models and access modes, and use
  supported native commands and Fast mode.
- Run the Coordinator and managed workers through the shared Local/ACP runtime with
  scoped tools, durable permission requests, bounded stop/cleanup and explicit recovery.
- Add an existing project through the directory picker and optionally preview/install
  guidance during adoption. Review required, prefilled project details before adding it.
  The Coordinator can configure and validate worker setup through
  its project-scoped tools without requiring installed repository guidance.
- Follow mandatory task stages and worker output in the task feed. Active output stays
  after new feed events; completed output stays in its attempt event. Focused activity
  updates replace output polling without refreshing the whole board for each chunk.
- Remove the fixed 15-minute agent execution cap while preserving explicit stop and bounded
  cleanup. Clarify current context tokens and omit unavailable usage totals from worker output.
- Confirm exact-result approval in the task composer, with an optional testing or approval
  comment. Standalone Record testing controls are removed; earlier observations remain readable.
- Simplify navigation with a permanent project rail, persistent project views, task context
  beside the Coordinator and shared scroll boundaries. Refine tooltips, relative timestamps,
  task overrides, action ordering and review colors; remove routine session-resume notices.
- Scroll board columns independently with fixed headings and thinner native scrollbars.
  Show active workers against capacity and make queued retries reflect the queue state.
- Remove the Decisions feature and retired worker runtime. Keep durable technical guidance
  in the repository and use task conversations for questions, feedback and review.
- Open the ready local service in a browser for interactive launches; use `--no-open` to
  suppress this. Noninteractive launches print the URL.

**Upgrade:** 0.2.0 establishes schema 44 as the supported storage baseline. The 0.1.0 and
intermediate rehearsal schemas are unsupported and are refused without modification;
there is no automatic migration from them. Stop Flowfield and preserve the old workspace
before upgrading. Use a separate data directory for a fresh workspace when needed;
existing schema-44 workspaces open unchanged. Never delete old state to bypass this check.

Install the matching managed bridge with `flowfield harness install codex`; keep native
Codex installed and signed in. Review/update installed project guidance separately and
start a fresh standalone coding conversation afterward. Restart Flowfield after upgrading;
the worker queue starts paused. Read the installation and storage docs before resuming work.

## 0.1.0

Initial Flowfield release: a shared task feed and workspace for agent software development.

- Adopt existing repositories and keep task intent, activity, questions, answers and results
  together in a durable conversation. Group tasks into milestones and track dependencies.
- Use the board and Needs you to prioritize work, answer questions and review results.
- Run independent tasks in isolated Git checkouts with configurable worker capacity and
  a paused-by-default queue. Codex is the first supported worker harness.
- Inspect and try an exact result, then explicitly approve delivery into the project checkout.
  Changed code or destination requires fresh approval; checkout blockers retain approval.
- Connect coordinators through MCP and installed project guidance. The CLI and web app use
  the same local service and persisted workspace.
- Preserve work through tested schema upgrades with verified database snapshots and guarded
  offline recovery. Existing supported Flowfield schemas 29 and 30 upgrade to schema 31.
- Share persistent notifications and dismissals across browsers and restarts. Check for
  compatible PyPI releases on web startup, hourly or manually, with release links and
  uv/pip upgrade instructions.

Install with `uv tool install flowfield-core`, or pip in a dedicated Python
environment. The package bundles its browser UI and agent guidance. macOS and Linux with
Python 3.12 are the release test targets; managed work requires an installed, signed-in
Codex CLI. Additional harnesses are planned.

**Upgrade:** stop Flowfield before changing versions and restart afterward. Supported database
migrations run automatically after a verified snapshot; the queue starts paused. Read the installation and storage docs
before upgrading and retain a full workspace backup for artifacts and disaster recovery.
