# Browser artifacts demo

[Watch the recording](browser-artifacts-demo.mp4).

Real Pi worker (`openai-codex/gpt-5.5`), Flowfield service and Chromium:

1. Watch the worker fill a quantity, add items and confirm checkout in **Live**.
2. Inspect the published PNG screenshot and Markdown check report.
3. Play the worker's saved WebM recording in the artifact gallery.

The demo uses a disposable local shop. No mocked media endpoints or agent responses.
The video crops the unused coordinator pane and removes idle time.

# Pi demo

[Watch the recording](pi-demo.mp4).

Unmocked Flowfield service with Pi 1.1.0 and `openai-codex/gpt-5.5`:

1. Empty coordinator settings select installed Pi, with Codex marked not installed.
2. Pi coordinator schedules a prepared task through scoped MCP.
3. Pi worker implements `slugify` in a separate worktree; four unittest cases pass.
4. Browser shows checks and the diff, then approves delivery to `main`.
5. Resumed Pi coordinator verifies delivery and test results.

The demo uses a disposable repository. No Codex executable or ACP bridge was used.
