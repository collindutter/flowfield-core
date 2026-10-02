# Changelog

Reviewed release notes describe changes by user impact. See CONTRIBUTING.md for versioning
and release preparation. A version heading becomes a release when its package is published.

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

Install with `uv tool install --python 3.12 flowfield-core`, or pip in a dedicated Python
environment. The package bundles its browser UI and agent guidance. macOS and Linux with
Python 3.12 are the release test targets; managed work requires an installed, signed-in
Codex CLI. Additional harnesses are planned.

**Upgrade:** stop Flowfield before changing versions and restart afterward. Supported database
migrations run automatically after a verified snapshot; the queue starts paused. Read the installation and storage docs
before upgrading and retain a full workspace backup for artifacts and disaster recovery.
