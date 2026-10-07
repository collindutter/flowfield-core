<h1 align="center">Flowfield</h1>

<p align="center"><strong>A workspace for you and your coding agents.</strong></p>

<p align="center">
  <a href="https://pypi.org/project/flowfield-core/"><img src="https://img.shields.io/pypi/v/flowfield-core?logo=pypi&amp;logoColor=white&amp;color=2563eb" alt="PyPI version"></a>
  <a href="https://github.com/flowfield-sh/flowfield-core/actions/workflows/tests.yml"><img src="https://img.shields.io/github/actions/workflow/status/flowfield-sh/flowfield-core/tests.yml?branch=main&amp;label=tests" alt="Tests"></a>
  <a href="https://docs.flowfield.sh/getting-started"><img src="https://github.com/flowfield-sh/flowfield-core/actions/workflows/docs.yml/badge.svg?branch=main" alt="Documentation"></a>
</p>

![Flowfield board with priorities, questions and results ready for review](https://raw.githubusercontent.com/flowfield-sh/flowfield-core/main/docs/images/board-overview.png)

**Plan in a conversation. Build in parallel. Review in one place.**

Flowfield puts Coordinator Chat beside a visual task board. Turn an idea into a plan,
prioritize cards, and let workers take on independent tasks while you keep planning.
Open a card to discuss it with the coordinator and follow its work from definition to review.

- **Coordinator Chat.** Turn ideas into milestones and tasks, adjust scope, and keep planning while workers build.
- **Task board and feeds.** See what’s queued, running or ready for review. Each card keeps its definition, progress, questions and results together.
- **Parallel work.** Independent tasks run in separate checkouts; dependencies hold work until it's ready.
- **Explicit review.** Review an exact result, request changes when needed, and approve delivery to your project.

## From idea to reviewed code

Start with a concrete request:

> Help me add CSV export. Review the existing code, suggest a first task, and identify what we need to decide before implementing it.

Shape the task with the coordinator, move it to **Up next**, and run the queue. Follow
progress and answer questions in its feed. When a result is ready, inspect the diff, try
it locally, and approve it or request changes—all beside the same conversation.

Independent tasks can run together. Dependencies keep follow-up work waiting for the
code it needs, and **Needs you** brings questions and reviews back to you.

## Install

Install and sign in to [Codex CLI](https://developers.openai.com/codex/cli/), then:

```sh
uv tool install flowfield-core
flowfield harness install codex
flowfield serve
```

The browser opens at [localhost:8765](http://127.0.0.1:8765). Use `--no-open` to keep it closed.
The browser UI is included.
See [Installation](https://docs.flowfield.sh/installation) for the pip alternative.

Choose **Add project** in the sidebar and select an existing directory, or register it from a terminal:

```sh
flowfield project init
```

Start planning in [Coordinator Chat](https://docs.flowfield.sh/coordinator), or connect a
[standalone coding agent](https://docs.flowfield.sh/integrations/codex#connect-a-standalone-coordinator).
Built-in chat and workers use your Codex login through the managed ACP runtime.
Codex is the first supported harness; further integrations are planned.

## Fits your existing project

Choose an existing repository and use its tools, setup commands and tests. Keep architecture,
designs and coding conventions in your repository; Flowfield keeps ongoing work on the
board and execution evidence in task feeds.

Workers use separate Git checkouts. You review an exact code result before Flowfield
delivers it to your project’s configured branch. Your working changes stay protected.

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
