# Issues and development

Bug reports, feedback and feature requests are welcome in [GitHub Issues](https://github.com/flowfield-sh/flowfield-core/issues).
We are not accepting external pull requests at this time.

For a bug, include the Flowfield version, operating system, steps to reproduce, expected
behavior and actual behavior. Remove secrets and private project content from logs or screenshots.
Public work issues describe outcomes and meaningful progress; completed implementations
identify the release that contains them.

## Develop locally

Python 3.12+, uv, Node 24+ and pnpm 11.19.0 are required.

```sh
make setup
make check
pnpm --dir web exec playwright install chromium
make smoke
make build
```

`make check` runs Python formatting, lint, types and deterministic tests, generated API
type validation, frontend checks/build, and Mintlify validation/link checks. `make smoke`
runs Chromium against the built application. `make build` produces a wheel and source
archive with the compiled UI. Both install without Node, pnpm or a checkout at runtime.
Use `scripts/check-install.py` from a clean installed environment to exercise the package.
`make check-dist` installs both distributions through pip and the wheel through isolated
`uv tool install`, then exercises each installed application outside the checkout.

For frontend development, run `uv run flowfield serve` and `pnpm --dir web dev` in separate
terminals. API wire types are generated with `make api-types`. Application operations live
under `src/flowfield/`; harness-specific behavior belongs in `src/flowfield/adapters/`.

Ordinary checks use isolated disposable state and never launch live model calls.

## Database changes

Schema 29 is the immutable initialization baseline, captured in
`tests/fixtures/schema_29.sql`. Append each schema change to the ordered registry in
`src/flowfield/migrations.py`; do not edit previous migrations or the baseline SQL.
Migration callbacks change only the database using `execute`/`executemany`. The storage
owner controls the transaction, version, migration history and pre-upgrade snapshot;
callbacks must not commit, use `executescript`, or mutate project files or artifacts.

Test fresh initialization and upgrading populated fixtures, including failure rollback,
interruption, restart and preserved application bindings. Keep application writes inside
`Workspace.connection()` so recovery can detect newer work. Automatic snapshots cover
the database; full workspace backups remain separate.

## Documentation

Mintlify content and configuration live in `docs/`. Its CLI is pinned and locked locally:

```sh
make docs
make docs-serve
```

`make docs` validates the build and internal links. `make docs-serve` starts a local
preview. Hosted deployment uses the `docs/` subdirectory and its `docs.json` configuration.
Keep user documentation concise and accurate for the behavior being released.

## Versioning and releases

The first release is **0.1.0**. `pyproject.toml` owns the version; uv keeps its entry in
`uv.lock` aligned. Tags use `vX.Y.Z`. During 0.x, patches contain compatible fixes;
minor releases contain new capabilities or breaking changes, with explicit upgrade notes.
From 1.0 onward, Semantic Versioning applies: major for breaking changes, minor for
compatible features, patch for compatible fixes. Compatibility covers documented CLI/MCP
interfaces, integration guidance and persisted workspace state. Schema changes still need
tested migrations and recovery; a version bump does not permit discarding user state.

Every push to main runs the `Tests` workflow: Python 3.12 tests on Linux/macOS, Python and
frontend quality checks, generated API validation, Mintlify validation, Chromium journeys
and clean installed-package checks. Live model calls are separate from CI.

### Prepare and rehearse

Review all commits and the aggregate diff since the last published release, then write a
nonempty `## X.Y.Z` section in `CHANGELOG.md`, newest first. Include features, fixes and
upgrade requirements by user impact. Preserve previous entries and verify each claim against
implementation, documentation and checks. Commit reviewed notes before releasing.

```sh
make setup
pnpm --dir web exec playwright install chromium
make release-check
uv run --no-sync python scripts/release.py 0.1.0 --dry-run
```

`release-check` runs local checks, browser journeys, clean builds, pip wheel/source and uv tool
installation checks, and distribution metadata validation. Build clears the generated `dist/`
directory. The preview checks branch, clean state, remotes, version, notes and unused tags,
and prints outgoing commits; it does not run checks or change files/refs.

The `Release` workflow in `.github/workflows/workflow.yml` also supports a manual run on
main. It runs CI, builds distributions once, and verifies those same artifacts on Linux/macOS.
Manual runs stop after verification. Artifacts and checksums remain available for 30 days.

### Publish

Configure the PyPI Trusted Publisher for owner `flowfield-sh`, repository `flowfield-core`,
workflow `workflow.yml` and environment `pypi`. The matching GitHub environment permits only
tags matching `v*`. Publishing uses short-lived GitHub identity tokens.

After reviewing the preparation and explicitly authorizing publication, run from clean main:

```sh
uv run --no-sync python scripts/release.py 0.1.0
```

The command holds a repository release lock, checks identity/notes, bumps only the version
files when needed, runs verification, and commits those version files. It rechecks the
remote and atomically pushes main and the annotated tag, then watches the release run.
CI requires the tag to match package/lock metadata and identify a commit on main. Only
the tag-triggered publishing job uses the `pypi` environment and OIDC permission. It publishes
verified original artifacts, then publishes the GitHub Release with reviewed notes and checksums.
Check actual PyPI installation, update discovery and GitHub assets after publication.

### Recover

Failures preserve local state and completed steps. Never move a published tag or rebuild an
uploaded version. Before tagging, correct and review retained changes, then retry. Exit 75
means another local release owns the lock. If push fails, inspect local and remote refs before
retrying the same atomic push. If CI fails, inspect the existing run; source corrections need
a new commit/version/tag. For partial upload or GitHub failure, rerun only failed jobs to reuse
the original artifacts. Do not rerun the build after uploading. uv skips already uploaded
identical files; GitHub completes the existing draft/assets. Expired artifacts require recovering
and verifying the exact originals. A bad published release needs a new version.
