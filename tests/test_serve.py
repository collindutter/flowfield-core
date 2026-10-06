"""Browser opening is CLI-only, after readiness, once, and optional."""

import asyncio
from types import SimpleNamespace

import pytest
import uvicorn

from flowfield import serve


@pytest.mark.parametrize(
    "ready,interactive,enabled,expected",
    [(True, True, True, 1), (False, True, True, 0), (True, False, True, 0), (True, True, False, 0)],
)
def test_browser_starts_only_after_ready_once(
    monkeypatch, capsys, ready, interactive, enabled, expected
):
    events = []

    async def startup(server, sockets):
        events.append("ready")
        server.started = ready

    class Thread:
        def __init__(self, *, target, args, daemon):
            assert daemon
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(uvicorn.Server, "startup", startup)
    monkeypatch.setattr(serve.threading, "Thread", Thread)
    monkeypatch.setattr(serve.sys.stdin, "isatty", lambda: interactive)
    monkeypatch.setattr(serve.sys.stdout, "isatty", lambda: interactive)
    monkeypatch.setattr(serve.webbrowser, "open", lambda url: events.append(url) or True)
    server = serve.BrowserServer(uvicorn.Config("unused", port=8773), open_browser=enabled)
    asyncio.run(server.startup())
    asyncio.run(server.startup())
    assert events.count("http://127.0.0.1:8773") == expected
    if expected:
        assert events[:2] == ["ready", "http://127.0.0.1:8773"]
    assert ("http://127.0.0.1:8773" in capsys.readouterr().out) == ready


def test_browser_failure_is_nonfatal_and_keeps_link(monkeypatch, capsys):
    def fail(url):
        raise OSError("No browser")

    monkeypatch.setattr(serve.webbrowser, "open", fail)
    serve.BrowserServer._open("http://127.0.0.1:8765")
    assert "http://127.0.0.1:8765" in capsys.readouterr().err


def test_serve_cli_no_open_and_selected_port(monkeypatch):
    from typer.testing import CliRunner

    from flowfield.cli import app

    observed = []
    monkeypatch.setattr(
        serve.BrowserServer,
        "run",
        lambda self: observed.append(
            SimpleNamespace(port=self.config.port, open_browser=self.open_browser)
        ),
    )
    result = CliRunner().invoke(app, ["serve", "--no-open", "--port", "8773"])
    assert result.exit_code == 0, result.output
    assert observed[0].port == 8773 and not observed[0].open_browser
