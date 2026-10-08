"""Opt-in, run-owned Chromium sessions; not a sandbox for the agent's host access.

Each session owns a fresh profile and its own Playwright driver. No personal browser
is attached, no browser is downloaded, and live frames exist only in bounded memory.
"""

import asyncio
import importlib
import tempfile
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from flowfield.errors import ApplicationError

LAUNCH_TIMEOUT = 30.0
NAVIGATION_TIMEOUT = 15.0
CAPTURE_TIMEOUT = 5.0
CLOSE_TIMEOUT = 10.0
DRIVER_TIMEOUT = 5.0
FRAME_INTERVAL = 0.5
MAX_FRAME_BYTES = 4 * 1024 * 1024
MAX_SCREENSHOT_BYTES = 16 * 1024 * 1024
MAX_RETAINED_STATUSES = 128
MAX_RECORDING_SECONDS = 300.0
MAX_RECORDING_BYTES = 80 * 1024 * 1024


class BrowserInfo(BaseModel):
    active: bool = False
    recording: bool = False
    url: str | None = None
    problem: str | None = None
    # Only the agent-side caller may use this capability; never serialize it to viewers.
    cdp_url: str | None = Field(default=None, exclude=True)


class BrowserSessionError(RuntimeError):
    """An actionable failure of the optional managed browser."""


@dataclass
class _Session:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0
    status: BrowserInfo = field(default_factory=BrowserInfo)
    driver: Any = None
    launcher: Any = None
    context: Any = None
    monitor: asyncio.Task[None] | None = None
    video_dir: Path | None = None
    recording_started: float = 0
    finalized: list[Path] = field(default_factory=list)
    cleanup_uncertain: bool = False
    profile: Path | None = None
    pages: list[Any] = field(default_factory=list)
    videos: list[Any] = field(default_factory=list)
    frame: bytes | None = None
    frame_page: Any = None
    frame_time: float = float("-inf")


async def _bounded[T](operation: Awaitable[T], timeout: float) -> T:
    async with asyncio.timeout(timeout):
        return await operation


async def _settle[T](task: asyncio.Task[T]) -> T:
    """Retain ownership through repeated caller cancellation until cleanup finishes."""
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                return task.result()


def _recording_size(directory: Path) -> int:
    return sum(path.stat().st_size for path in directory.glob("*.webm") if path.is_file())


def _validate_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise BrowserSessionError("Browser URL must be http://, https://, or about:blank") from exc
    if url == "about:blank":
        return
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise BrowserSessionError("Browser URL must be http://, https://, or about:blank")


class BrowserSessions:
    def __init__(self) -> None:
        self._sessions: OrderedDict[str, _Session] = OrderedDict()

    @asynccontextmanager
    async def _locked(self, run_id: str) -> AsyncIterator[_Session]:
        session = self._sessions.setdefault(run_id, _Session())
        session.users += 1
        self._sessions.move_to_end(run_id)
        try:
            async with session.lock:
                yield session
        finally:
            session.users -= 1
            self._prune()

    def _prune(self) -> None:
        idle = [
            key
            for key, session in self._sessions.items()
            if not session.status.active
            and session.context is None
            and session.driver is None
            and session.launcher is None
            and not session.videos
            and not session.finalized
            and not session.users
        ]
        for key in idle[:-MAX_RETAINED_STATUSES]:
            del self._sessions[key]

    def info(self, run_id: str) -> BrowserInfo:
        session = self._sessions.get(run_id)
        if session is None:
            return BrowserInfo()
        page = self._page(session, required=False)
        if page is not None:
            session.status.url = str(page.url)
        return session.status.model_copy()

    async def open(
        self, run_id: str, runtime: Path, url: str = "about:blank", record_video: bool = False
    ) -> BrowserInfo:
        _validate_url(url)
        async with self._locked(run_id) as session:
            if session.status.active:
                if session.status.recording != record_video:
                    raise BrowserSessionError(
                        "Recording cannot be changed while a browser is active; close it first"
                    )
                return self.info(run_id)
            if session.context is not None or session.driver is not None or session.launcher:
                await self._protected_stop(session)
            if session.finalized or session.videos:
                raise BrowserSessionError("Close the previous browser to collect its videos first")
            startup = asyncio.create_task(self._start(session, runtime, url, record_video))
            try:
                await asyncio.shield(startup)
            except asyncio.CancelledError:
                # Launch may have spawned Chromium before returning its context. Adopt
                # that context first, then close it, instead of abandoning the launch.
                try:
                    await _settle(startup)
                except Exception:
                    pass
                await self._protected_stop(session)
                raise
            return self.info(run_id)

    async def _start(self, session: _Session, runtime: Path, url: str, record_video: bool) -> None:
        session.status = BrowserInfo()
        try:
            try:
                api = importlib.import_module("playwright.async_api")
            except ImportError as exc:
                raise BrowserSessionError(
                    "Managed browser requires the optional Playwright package. "
                    "Install it with `python -m pip install playwright`, then explicitly "
                    "install Chromium with `python -m playwright install chromium`."
                ) from exc
            runtime.mkdir(parents=True, exist_ok=True)
            session.profile = Path(tempfile.mkdtemp(prefix="browser-", dir=runtime)).resolve()
            session.launcher = api.async_playwright()
            session.driver = await _bounded(session.launcher.start(), DRIVER_TIMEOUT)
            options: dict[str, Any] = {
                "headless": True,
                "args": ["--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0"],
                "viewport": {"width": 1280, "height": 720},
                "timeout": LAUNCH_TIMEOUT * 1000,
            }
            if record_video:
                video_dir = session.profile.parent / (session.profile.name + "-videos")
                video_dir.mkdir()
                session.video_dir = video_dir
                session.recording_started = asyncio.get_running_loop().time()
                options["record_video_dir"] = str(video_dir)
                options["record_video_size"] = {"width": 1280, "height": 720}
            session.context = await _bounded(
                session.driver.chromium.launch_persistent_context(str(session.profile), **options),
                LAUNCH_TIMEOUT + 1,
            )
            session.context.on("page", lambda page: self._track_page(session, page))
            session.context.on("close", lambda: self._disconnected(session))
            for page in session.context.pages:
                self._track_page(session, page)
            cdp_url = await _bounded(self._cdp_url(session.profile), DRIVER_TIMEOUT)
            page = self._page(session, required=False)
            if page is None:
                page = await _bounded(session.context.new_page(), NAVIGATION_TIMEOUT)
                self._track_page(session, page)
            await _bounded(
                page.goto(url, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT * 1000),
                NAVIGATION_TIMEOUT + 1,
            )
            session.status = BrowserInfo(
                active=True, recording=record_video, url=str(page.url), cdp_url=cdp_url
            )
            if record_video:
                session.monitor = asyncio.create_task(self._monitor_recording(session))
        except BaseException as exc:
            message = str(exc) or type(exc).__name__
            if "Executable doesn't exist" in message or "executable doesn't exist" in message:
                message = (
                    "Chromium is not installed for Playwright. Run "
                    "`python -m playwright install chromium` explicitly; "
                    "Flowfield never downloads it."
                )
            session.status.problem = message[:1000]
            await self._protected_stop(session)
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise BrowserSessionError(session.status.problem) from exc

    async def _monitor_recording(self, session: _Session) -> None:
        started = session.recording_started
        while session.status.active:
            await asyncio.sleep(1)
            directory = session.video_dir
            size = 0
            if directory is not None:
                try:
                    size = await asyncio.to_thread(_recording_size, directory)
                except OSError:
                    pass  # The duration limit still applies if a file disappears during stat.
            elapsed = asyncio.get_running_loop().time() - started
            if elapsed < MAX_RECORDING_SECONDS and size < MAX_RECORDING_BYTES:
                continue
            async with session.lock:
                if not session.status.active:
                    return
                session.status.problem = "Recording limit reached (300 seconds or 80 MiB)"
                try:
                    await self._protected_stop(session)
                except ApplicationError:
                    return  # Retryable handles and cleanup problem remain visible.
                return

    async def _cdp_url(self, profile: Path) -> str:
        while True:
            try:
                port = int((profile / "DevToolsActivePort").read_text().splitlines()[0])
                if 0 < port < 65536:
                    return f"http://127.0.0.1:{port}"
            except (OSError, ValueError, IndexError):
                pass
            await asyncio.sleep(0.05)

    def _track_page(self, session: _Session, page: Any) -> None:
        if page in session.pages:
            return
        session.pages.append(page)
        video = page.video
        if video is not None:
            session.videos.append(video)
        page.on("close", lambda: self._forget_page(session, page))

    def _forget_page(self, session: _Session, page: Any) -> None:
        if page in session.pages:
            session.pages.remove(page)
        if session.frame_page is page:
            session.frame = None
            session.frame_page = None

    def _disconnected(self, session: _Session) -> None:
        session.status.active = False
        session.status.recording = False
        session.status.cdp_url = None
        session.frame = None
        session.frame_page = None

    def _page(self, session: _Session, *, required: bool = True) -> Any:
        if session.context is not None:
            # CDP clients can create pages; refresh as well as listening for events.
            for page in session.context.pages:
                self._track_page(session, page)
        for page in reversed(session.pages):
            if not page.is_closed():
                return page
        if required:
            raise BrowserSessionError("Managed browser has no open page")
        return None

    async def frame(self, run_id: str) -> bytes:
        async with self._locked(run_id) as session:
            self._require_active(session)
            page = self._page(session)
            now = asyncio.get_running_loop().time()
            if (
                session.frame is not None
                and session.frame_page is page
                and now - session.frame_time < FRAME_INTERVAL
            ):
                return session.frame
            # Throttle even across tab changes and failed captures, shared by all viewers.
            await asyncio.sleep(max(0, session.frame_time + FRAME_INTERVAL - now))
            session.frame_time = asyncio.get_running_loop().time()
            try:
                data = await _bounded(
                    page.screenshot(
                        type="jpeg", quality=65, full_page=False, timeout=CAPTURE_TIMEOUT * 1000
                    ),
                    CAPTURE_TIMEOUT + 1,
                )
                if len(data) > MAX_FRAME_BYTES:
                    raise BrowserSessionError("Browser frame exceeded the bounded capture size")
            except Exception as exc:
                session.frame = None
                session.status.problem = str(exc)[:1000]
                raise BrowserSessionError(str(exc)) from exc
            session.frame, session.frame_page = data, page
            session.status.url, session.status.problem = str(page.url), None
            return bytes(data)

    async def screenshot(self, run_id: str, path: Path) -> Path:
        async with self._locked(run_id) as session:
            self._require_active(session)
            page = self._page(session)
            try:
                data = await _bounded(
                    page.screenshot(type="png", full_page=False, timeout=CAPTURE_TIMEOUT * 1000),
                    CAPTURE_TIMEOUT + 1,
                )
                if len(data) > MAX_SCREENSHOT_BYTES:
                    raise BrowserSessionError(
                        "Browser screenshot exceeded the bounded capture size"
                    )
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            except Exception as exc:
                session.status.problem = str(exc)[:1000]
                raise BrowserSessionError(str(exc)) from exc
            session.status.url, session.status.problem = str(page.url), None
            return path

    def _require_active(self, session: _Session) -> None:
        if not session.status.active:
            raise BrowserSessionError("No active managed browser for this run; open it explicitly")

    async def close(self, run_id: str) -> list[Path]:
        async with self._locked(run_id) as session:
            await self._protected_stop(session)
            videos, session.finalized = session.finalized, []
            return videos

    async def _protected_stop(self, session: _Session) -> list[Path]:
        if session.monitor is not None and session.monitor is not asyncio.current_task():
            session.monitor.cancel()
            session.monitor = None
        task = asyncio.create_task(self._stop(session))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await _settle(task)
            raise

    async def _stop(self, session: _Session) -> list[Path]:
        problems: list[str] = []
        self._disconnected(session)
        session.status.recording = False
        if session.context is not None:
            try:
                self._page(session, required=False)
                await _bounded(session.context.close(), CLOSE_TIMEOUT)
            except Exception:
                pass  # The owned driver shutdown below is the fallback.
        # Never signal a PID read from the profile. A failed driver shutdown is
        # uncertain, not evidence of browser exit; retain handles for a retry.
        try:
            if session.driver is not None:
                await _bounded(session.driver.stop(), DRIVER_TIMEOUT)
            elif session.launcher is not None:
                await _bounded(session.launcher.__aexit__(None, None, None), DRIVER_TIMEOUT)
        except Exception as exc:
            problems.append(f"Browser driver cleanup failed: {str(exc) or type(exc).__name__}")
        if not problems:
            session.context = session.driver = session.launcher = None
            session.pages.clear()
            deadline = asyncio.get_running_loop().time() + CLOSE_TIMEOUT
            for video in list(session.videos):
                try:
                    remaining = max(0.001, deadline - asyncio.get_running_loop().time())
                    path = Path(await _bounded(video.path(), remaining))
                    if path.suffix != ".webm" or not path.is_file():
                        raise BrowserSessionError("Finalized WebM video is missing")
                    if path not in session.finalized:
                        session.finalized.append(path)
                    session.videos.remove(video)
                except Exception as exc:
                    problems.append(f"Video finalization failed: {str(exc) or type(exc).__name__}")
                    if asyncio.get_running_loop().time() >= deadline:
                        break
        if problems:
            session.cleanup_uncertain = True
            session.status.problem = "; ".join(problems)[:1000]
            raise ApplicationError("browser_cleanup_uncertain", session.status.problem, 503)
        if session.cleanup_uncertain:
            session.status.problem = None
            session.cleanup_uncertain = False
        return list(session.finalized)

    async def close_all(self) -> None:
        # Each close is cancellation-safe; finish all owners even if the caller leaves.
        async def finish() -> None:
            async def stop(run_id: str) -> None:
                async with self._locked(run_id) as session:
                    await self._protected_stop(session)

            results = await asyncio.gather(
                *(stop(run_id) for run_id in list(self._sessions)), return_exceptions=True
            )
            for result in results:
                if isinstance(result, BaseException):
                    raise result

        task = asyncio.create_task(finish())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await _settle(task)
            raise
