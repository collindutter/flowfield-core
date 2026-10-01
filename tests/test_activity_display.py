"""Readable public activity preserves evidence and real ownership; no model calls."""

import base64

from test_execution import BASE, fixture

from flowfield.activity_text import preview, retain
from flowfield.adapters.activity_diff import captured_changes
from flowfield.adapters.codex_activity import CodexActivity
from flowfield.adapters.local_environment import git
from flowfield.execution_models import Usage
from flowfield.run_activity import ActivityUpdate, RunActivity
from flowfield.worker_context import brief_context


def test_display_collapses_source_and_keeps_command_failure():
    script = "python - <<'PY'\n" + "print('code')\n" * 1000 + "PY\nExit code: 17"
    kept = retain(script, 6000)
    shown = preview(kept, "command")
    assert shown.startswith("python - <<'PY'") and shown.endswith("Exit code: 17")
    assert "print('code')" not in shown and "source collapsed" in shown
    assert "print('code')" in kept and "middle omitted" in kept
    assert (
        preview("test -f result.txt\nExit code: 0", "command") == "test -f result.txt\nExit code: 0"
    )
    shown = preview("Answer\n```python\nprint('source')\n```\nImportant limitation", "agent")
    assert "source" not in shown and "Code block collapsed" in shown
    assert "Answer" in shown and "Important limitation" in shown
    text = "Header\n" + "a line\n" * 100 + "Failure at the end"
    shown = preview(text, "output")
    assert shown.startswith("Header") and shown.endswith("Failure at the end")
    assert "middle omitted" in shown and len(shown) <= 900


def test_usage_updates_without_new_output_and_retained_text_survives_append(tmp_path):
    execution = fixture(tmp_path)
    run = execution.claim("harbor", BASE, {})
    store = RunActivity(execution.workspace)
    store.write(
        "harbor",
        run.id,
        [ActivityUpdate(key="output", kind="output", text="Header\n" + "x" * 7000)],
    )
    store.write(
        "harbor",
        run.id,
        [ActivityUpdate(key="output", kind="output", text="\nFailure at the end", append=True)],
    )
    page = store.read("harbor", run.id)
    item = page.items[0]
    assert item.text.startswith("Header") and item.text.endswith("Failure at the end")
    assert len(item.text) <= 6000 and item.omitted and item.abridged
    assert len(item.preview) <= 900 and "Failure at the end" in item.preview
    execution.usage("harbor", run.id, Usage(total_tokens=240, cached_input_tokens=None))
    unchanged = store.read("harbor", run.id, page.revision)
    assert not unchanged.changed and not unchanged.items
    assert unchanged.usage.total_tokens == 240 and unchanged.usage.cached_input_tokens is None
    assert not unchanged.usage.complete


def test_adapter_bounded_stream_retains_received_start_and_end():
    events = []
    adapter = CodexActivity(events.append)
    for chunk in (b"Header\n" + b"a" * 16000, b"\nFinal error"):
        adapter.event(
            "command/exec/outputDelta",
            {
                "processId": "owned",
                "stream": "stderr",
                "deltaBase64": base64.b64encode(chunk).decode(),
            },
            None,
            None,
            {"owned"},
        )
    output = adapter.output["owned"]["stderr"]
    assert len(output) <= 12000 and output.startswith(b"Header") and output.endswith(b"Final error")
    assert "middle omitted" in events[-1].text and events[-1].omitted


def test_captured_file_counts_come_from_git_trees_including_unusual_names(tmp_path):
    git(tmp_path, "init")
    (tmp_path / "base.txt").write_text("one\ntwo\n")

    def commit():
        git(tmp_path, "add", ".")
        git(
            tmp_path,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "fixture",
        )
        return git(tmp_path, "rev-parse", "HEAD").decode().strip()

    before = commit()
    (tmp_path / "base.txt").write_text("one\nnew\nthird\n")
    (tmp_path / "tab\tname.txt").write_text("added\n")
    (tmp_path / "binary.dat").write_bytes(b"\0\1\2")
    after = commit()
    summary = captured_changes(tmp_path, before, after)
    assert '"base.txt": +2 / −1 lines' in summary
    assert '"tab\\tname.txt": +1 / −0 lines' in summary
    assert '"binary.dat": binary change' in summary
    assert "no file changes" in captured_changes(tmp_path, after, after)


def test_discussion_brief_keeps_question_and_sources_without_exhaustive_read_policy():
    sections = {
        "description": "Scope " * 1000,
        "feedback": "Question " * 600,
        "selected_result": "Bound result",
        "previous_reply": "Earlier answer",
    }
    work, discussion = brief_context(sections), brief_context(sections, discussion=True)
    assert len(discussion["description"]) == 1500 and len(work["description"]) == 6000
    assert discussion["feedback"] == work["feedback"]
    assert set(discussion["truncated_sections"]) == {"description", "feedback"}
    assert discussion["sections"]["selected_result"] == len("Bound result")
    assert "Read attempt_history for orientation" not in discussion["history_policy"]
    assert "Read attempt_history for orientation" in work["history_policy"]
