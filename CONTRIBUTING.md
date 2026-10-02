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
