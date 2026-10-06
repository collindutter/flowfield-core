"""CLI-only browser launch after the local listener is ready."""

import socket
import sys
import threading
import webbrowser

import uvicorn


class BrowserServer(uvicorn.Server):
    def __init__(self, config: uvicorn.Config, *, open_browser: bool):
        super().__init__(config)
        self.open_browser = open_browser
        self._opened = False

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets)
        if not self.started or self._opened:
            return
        self._opened = True
        url = f"http://127.0.0.1:{self.config.port}"
        print(f"Flowfield: {url}", flush=True)
        if self.open_browser and sys.stdin.isatty() and sys.stdout.isatty():
            # A desktop browser launcher must never hold up service startup or exit.
            threading.Thread(target=self._open, args=(url,), daemon=True).start()

    @staticmethod
    def _open(url: str) -> None:
        try:
            opened = webbrowser.open(url)
        except Exception:
            opened = False
        if not opened:
            print(f"Open {url} in your browser.", file=sys.stderr, flush=True)
