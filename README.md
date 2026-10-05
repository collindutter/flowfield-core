<h1 align="center">Flowfield</h1>

<p align="center"><strong>A workspace for you and your coding agents.</strong></p>

<p align="center">
  <a href="https://pypi.org/project/flowfield-core/"><img src="https://img.shields.io/pypi/v/flowfield-core?logo=pypi&amp;logoColor=white&amp;color=2563eb" alt="PyPI version"></a>
  <a href="https://github.com/flowfield-sh/flowfield-core/actions/workflows/tests.yml"><img src="https://img.shields.io/github/actions/workflow/status/flowfield-sh/flowfield-core/tests.yml?branch=main&amp;label=tests" alt="Tests"></a>
  <a href="https://docs.flowfield.sh/getting-started"><img src="https://github.com/flowfield-sh/flowfield-core/actions/workflows/docs.yml/badge.svg?branch=main" alt="Documentation"></a>
</p>

![Flowfield board with priorities, questions and results ready for review](https://raw.githubusercontent.com/flowfield-sh/flowfield-core/main/docs/images/board-overview.png)

Plan work, run tasks in parallel, and review results in one workspace. Keep planning with
your coordinator while workers implement tasks; use the board to see progress and what
needs your judgment.

- **Task feeds.** Each task keeps its intent, progress, questions, feedback and results together.
- **Parallel work.** Independent tasks run in separate checkouts; dependencies hold work until it's ready.
- **Explicit review.** Review an exact result, request changes when needed, and approve delivery to your project.

## Install

```sh
uv tool install --python 3.12 flowfield-core
flowfield serve
```

Open [localhost:8765](http://127.0.0.1:8765). The browser UI is included.
See [Installation](https://docs.flowfield.sh/installation) for the pip alternative.

Choose **Add project** in the sidebar and select an existing directory, or register it from a terminal:

```sh
flowfield project init
```

Start planning in [Coordinator Chat](https://docs.flowfield.sh/coordinator), or connect a
[standalone coding agent](https://docs.flowfield.sh/integrations/codex#connect-a-standalone-coordinator).
Built-in chat and workers use your installed, signed-in Codex CLI and managed ACP runtime.
Codex is the first supported harness; further integrations are planned.

## Documentation

[Getting Started](https://docs.flowfield.sh/getting-started) · [Concepts](https://docs.flowfield.sh/concepts) ·
[CLI](https://docs.flowfield.sh/cli) · [Integrations](https://docs.flowfield.sh/integrations/overview)

## Build from source

Requires Python 3.12+, uv, Node 24+ and pnpm 11.19.0.

```sh
make setup
pnpm --dir web build
uv run flowfield serve
```

See [CONTRIBUTING.md](https://github.com/flowfield-sh/flowfield-core/blob/main/CONTRIBUTING.md) for checks and issue reporting.
Report vulnerabilities through [private security reporting](https://github.com/flowfield-sh/flowfield-core/security/advisories/new).

## License

[Apache License 2.0](https://github.com/flowfield-sh/flowfield-core/blob/main/LICENSE).
