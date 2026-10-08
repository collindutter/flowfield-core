"""Managed browser ownership tests; no real browser or optional package required."""

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from flowfield.adapters import browser_session as browser
from flowfield.errors import ApplicationError


class FakeVideo:
    def __init__(self, path):
        self.file = path
        self.ready = False
        self.fail = False

    async def path(self):
        if self.fail or not self.ready:
            raise RuntimeError("video not finalized")
        return str(self.file)

    def finalize(self):
        self.file.write_bytes(b"webm")
        self.ready = True


class FakePage:
    def __init__(self, context):
        self.context = context
        self.url = "about:blank"
        self.closed = False
        self.handlers = {}
        self.captures = []
        self.capture_error = None
        self.jpeg = b"\xff\xd8jpeg"
        self.video = None
        if context.video_dir:
            self.video = FakeVideo(context.video_dir / f"{len(context.pages)}.webm")

    def on(self, event, callback):
        self.handlers[event] = callback

    def is_closed(self):
        return self.closed

    async def goto(self, url, **kwargs):
        if self.context.driver.navigation_error:
            raise RuntimeError("navigation failed")
        self.url = url

    async def screenshot(self, **kwargs):
        self.captures.append(kwargs)
        if self.capture_error:
            raise self.capture_error
        return self.jpeg if kwargs["type"] == "jpeg" else b"\x89PNG"

    async def close(self):
        self.closed = True
        if self.video:
            self.video.finalize()
        if "close" in self.handlers:
            self.handlers["close"]()


class FakeContext:
    def __init__(self, driver, video_dir):
        self.driver = driver
        self.video_dir = Path(video_dir) if video_dir else None
        self.pages = []
        self.handlers = {}
        self.close_calls = 0

    def on(self, event, callback):
        self.handlers[event] = callback

    async def new_page(self, *, emit=True):
        page = FakePage(self)
        self.pages.append(page)
        if emit and "page" in self.handlers:
            self.handlers["page"](page)
        return page

    async def close(self):
        self.close_calls += 1
        self.driver.close_entered.set()
        if self.driver.close_gate:
            await self.driver.close_gate.wait()
        if self.driver.close_error:
            raise RuntimeError("close failed")
        for page in self.pages:
            if not page.closed:
                await page.close()
        if "close" in self.handlers:
            self.handlers["close"]()


class FakeDriver:
    def __init__(self):
        self.chromium = self
        self.context = None
        self.options = None
        self.profile = None
        self.stopped = 0
        self.launch_gate = None
        self.launch_entered = asyncio.Event()
        self.close_gate = None
        self.close_entered = asyncio.Event()
        self.launch_error = None
        self.close_error = False
        self.stop_error = False
        self.navigation_error = False

    async def launch_persistent_context(self, profile, **options):
        self.profile, self.options = Path(profile), options
        self.launch_entered.set()
        if self.launch_gate:
            await self.launch_gate.wait()
        if self.launch_error:
            raise RuntimeError(self.launch_error)
        self.profile.joinpath("DevToolsActivePort").write_text("12345\n/devtools/browser/owned\n")
        self.context = FakeContext(self, options.get("record_video_dir"))
        await self.context.new_page()
        return self.context

    async def stop(self):
        self.stopped += 1
        if self.stop_error:
            raise RuntimeError("driver stuck")
        if self.context:
            for page in self.context.pages:
                if not page.closed:
                    await page.close()


class FakeLauncher:
    def __init__(self, driver):
        self.driver = driver

    async def start(self):
        return self.driver

    async def __aexit__(self, *args):
        await self.driver.stop()


@pytest.fixture
def fake(monkeypatch):
    drivers = []
    queued = []

    def factory():
        driver = queued.pop(0) if queued else FakeDriver()
        drivers.append(driver)
        return FakeLauncher(driver)

    real_import = browser.importlib.import_module

    def import_module(name):
        if name == "playwright.async_api":
            return SimpleNamespace(async_playwright=factory)
        return real_import(name)

    monkeypatch.setattr(browser.importlib, "import_module", import_module)
    return SimpleNamespace(drivers=drivers, queued=queued)


def test_opt_in_unique_profiles_and_private_cdp(fake, tmp_path):
    async def exercise():
        sessions = browser.BrowserSessions()
        assert not sessions.info("one").active
        with pytest.raises(browser.BrowserSessionError, match="open it explicitly"):
            await sessions.frame("one")
        assert fake.drivers == []
        first = await sessions.open("one", tmp_path, "https://example.test")
        assert first.active and first.cdp_url == "http://127.0.0.1:12345"
        assert "cdp_url" not in first.model_dump()
        assert "cdp_url" not in first.model_dump_json()
        first.active = False
        assert sessions.info("one").active
        await sessions.open("one", tmp_path)
        await sessions.open("two", tmp_path)
        assert len(fake.drivers) == 2
        assert fake.drivers[0].profile != fake.drivers[1].profile
        options = fake.drivers[0].options
        assert options["headless"] is True
        assert "--remote-debugging-address=127.0.0.1" in options["args"]
        assert "--remote-debugging-port=0" in options["args"]
        assert "record_video_dir" not in options
        assert not list(tmp_path.rglob("*.jpg"))
        await sessions.close_all()
        assert all(driver.stopped == 1 for driver in fake.drivers)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "about:config",
        "ftp://example.test",
        "http:///bad",
    ],
)
def test_rejected_urls_do_not_launch(fake, tmp_path, url):
    with pytest.raises(browser.BrowserSessionError, match="Browser URL"):
        asyncio.run(browser.BrowserSessions().open("run", tmp_path, url))
    assert not fake.drivers


def test_capture_throttle_errors_size_bound_and_new_tabs(fake, tmp_path, monkeypatch):
    async def exercise():
        sessions = browser.BrowserSessions()
        await sessions.open("run", tmp_path)
        context = fake.drivers[0].context
        first = context.pages[0]
        frames = await asyncio.gather(*(sessions.frame("run") for _ in range(10)))
        assert len(first.captures) == 1 and all(frame == frames[0] for frame in frames)
        second = await context.new_page(emit=False)  # External CDP, even before page event.
        second.url = "https://example.test/new"
        monkeypatch.setattr(browser, "FRAME_INTERVAL", 0)
        await sessions.frame("run")
        assert len(second.captures) == 1
        assert sessions.info("run").url == second.url
        shot = tmp_path / "shots" / "image.png"
        assert await sessions.screenshot("run", shot) == shot
        assert shot.read_bytes() == b"\x89PNG"
        await second.close()
        await sessions.frame("run")
        assert len(first.captures) == 2
        first.jpeg = b"x" * (browser.MAX_FRAME_BYTES + 1)
        with pytest.raises(browser.BrowserSessionError, match="bounded"):
            await sessions.frame("run")
        first.capture_error = RuntimeError("capture failed")
        with pytest.raises(browser.BrowserSessionError, match="capture failed"):
            await sessions.frame("run")
        assert sessions.info("run").problem == "capture failed"
        await first.close()
        with pytest.raises(browser.BrowserSessionError, match="no open page"):
            await sessions.frame("run")
        await sessions.close("run")

    asyncio.run(exercise())


def test_recording_finalizes_all_tabs_and_rejects_toggle(fake, tmp_path):
    async def exercise():
        sessions = browser.BrowserSessions()
        assert (await sessions.open("run", tmp_path, record_video=True)).recording
        with pytest.raises(browser.BrowserSessionError, match="Recording cannot"):
            await sessions.open("run", tmp_path, record_video=False)
        await sessions.open("run", tmp_path, record_video=True)
        context = fake.drivers[0].context
        await context.new_page()
        await context.pages[0].close()
        videos = await sessions.close("run")
        assert len(videos) == 2 and all(path.read_bytes() == b"webm" for path in videos)
        assert not sessions.info("run").active
        assert not sessions.info("run").recording
        assert await sessions.close("run") == []
        await sessions.open("run", tmp_path)
        assert "record_video_dir" not in fake.drivers[-1].options
        await sessions.close_all()

    asyncio.run(exercise())


def test_missing_dependency_and_chromium_are_actionable(monkeypatch, tmp_path):
    def missing(name):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(browser.importlib, "import_module", missing)
    sessions = browser.BrowserSessions()
    with pytest.raises(browser.BrowserSessionError, match="pip install playwright"):
        asyncio.run(sessions.open("run", tmp_path))
    assert "install chromium" in sessions.info("run").problem


def test_startup_errors_clean_owned_driver(fake, tmp_path):
    async def exercise():
        for error, navigation in [("Executable doesn't exist at path", False), (None, True)]:
            driver = FakeDriver()
            driver.launch_error, driver.navigation_error = error, navigation
            fake.queued.append(driver)
            sessions = browser.BrowserSessions()
            with pytest.raises(browser.BrowserSessionError) as raised:
                await sessions.open("run", tmp_path)
            assert (
                "install chromium" in str(raised.value)
                if error
                else "navigation" in str(raised.value)
            )
            assert driver.stopped == 1
            assert not sessions.info("run").active

    asyncio.run(exercise())


def test_cancelled_launch_adopts_context_and_stops_it(fake, tmp_path):
    async def exercise():
        driver = FakeDriver()
        driver.launch_gate = asyncio.Event()
        fake.queued.append(driver)
        sessions = browser.BrowserSessions()
        opening = asyncio.create_task(sessions.open("run", tmp_path))
        await driver.launch_entered.wait()
        opening.cancel()
        await asyncio.sleep(0)
        opening.cancel()
        driver.launch_gate.set()
        with pytest.raises(asyncio.CancelledError):
            await opening
        assert driver.context.close_calls == 1 and driver.stopped == 1
        assert not sessions.info("run").active

    asyncio.run(exercise())


def test_cancelled_close_finishes_recording_before_releasing_lock(fake, tmp_path):
    async def exercise():
        sessions = browser.BrowserSessions()
        await sessions.open("run", tmp_path, record_video=True)
        driver = fake.drivers[0]
        driver.close_gate = asyncio.Event()
        closing = asyncio.create_task(sessions.close("run"))
        await driver.close_entered.wait()
        closing.cancel()
        closing.cancel()
        next_close = asyncio.create_task(sessions.close("run"))
        driver.close_gate.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert len(await next_close) == 1
        assert driver.stopped == 1

    asyncio.run(exercise())


def test_failed_close_uses_driver_fallback_and_uncertain_stop_is_retryable(fake, tmp_path):
    async def exercise():
        sessions = browser.BrowserSessions()
        await sessions.open("run", tmp_path, record_video=True)
        driver = fake.drivers[0]
        driver.close_error = driver.stop_error = True
        with pytest.raises(ApplicationError) as raised:
            await sessions.close("run")
        assert raised.value.code == "browser_cleanup_uncertain"
        assert not sessions.info("run").active
        assert "driver stuck" in sessions.info("run").problem
        with pytest.raises(ApplicationError):
            await sessions.open("run", tmp_path)
        assert len(fake.drivers) == 1
        driver.stop_error = False
        assert len(await sessions.close("run")) == 1
        assert sessions.info("run").problem is None

    asyncio.run(exercise())


def test_launch_timeout_is_bounded_and_cleans_driver(fake, tmp_path, monkeypatch):
    async def exercise():
        driver = FakeDriver()
        driver.launch_gate = asyncio.Event()
        fake.queued.append(driver)
        monkeypatch.setattr(browser, "LAUNCH_TIMEOUT", -0.99)  # Wrapper deadline is +1s.
        sessions = browser.BrowserSessions()
        with pytest.raises(browser.BrowserSessionError, match="TimeoutError"):
            await sessions.open("run", tmp_path)
        assert driver.stopped == 1

    asyncio.run(exercise())


def test_recording_auto_stop_retains_videos_and_status(fake, tmp_path, monkeypatch):
    async def exercise():
        monkeypatch.setattr(browser, "MAX_RECORDING_SECONDS", 0)
        sessions = browser.BrowserSessions()
        await sessions.open("run", tmp_path, record_video=True)
        await asyncio.sleep(1.1)
        assert not sessions.info("run").active
        assert "Recording limit" in sessions.info("run").problem
        with pytest.raises(browser.BrowserSessionError, match="collect its videos"):
            await sessions.open("run", tmp_path)
        assert len(await sessions.close("run")) == 1

    asyncio.run(exercise())


def test_retained_idle_statuses_are_bounded(fake, tmp_path, monkeypatch):
    async def exercise():
        monkeypatch.setattr(browser, "MAX_RETAINED_STATUSES", 2)
        sessions = browser.BrowserSessions()
        await sessions.open("active", tmp_path)
        for index in range(8):
            await sessions.close(str(index))
        assert len(sessions._sessions) == 3
        assert sessions.info("active").active
        await sessions.close_all()

    asyncio.run(exercise())


def test_capture_and_close_timeouts_are_bounded(fake, tmp_path, monkeypatch):
    async def exercise():
        original_bounded = browser._bounded

        async def short_bound(operation, timeout):
            return await original_bounded(operation, min(timeout, 0.01))

        monkeypatch.setattr(browser, "_bounded", short_bound)
        sessions = browser.BrowserSessions()
        await sessions.open("run", tmp_path)
        driver = fake.drivers[0]

        async def hung_capture(**kwargs):
            await asyncio.Future()

        driver.context.pages[0].screenshot = hung_capture
        with pytest.raises(browser.BrowserSessionError):
            await sessions.frame("run")
        driver.close_gate = asyncio.Event()
        assert await sessions.close("run") == []
        assert driver.stopped == 1  # Bounded close failure uses owned driver fallback.

    asyncio.run(exercise())


def test_frame_rate_is_shared_across_tab_changes(fake, tmp_path):
    async def exercise():
        sessions = browser.BrowserSessions()
        await sessions.open("run", tmp_path)
        await sessions.frame("run")
        first_started = sessions._sessions["run"].frame_time
        page = await fake.drivers[0].context.new_page()
        await sessions.frame("run")
        second_started = sessions._sessions["run"].frame_time
        assert second_started - first_started >= browser.FRAME_INTERVAL
        assert len(page.captures) == 1
        await sessions.close_all()

    asyncio.run(exercise())


def test_driver_start_timeout_uses_launcher_cleanup(fake, tmp_path, monkeypatch):
    async def exercise():
        driver = FakeDriver()

        class HungLauncher(FakeLauncher):
            async def start(self):
                await asyncio.Future()

        monkeypatch.setattr(
            browser.importlib,
            "import_module",
            lambda name: SimpleNamespace(async_playwright=lambda: HungLauncher(driver)),
        )
        monkeypatch.setattr(browser, "DRIVER_TIMEOUT", 0.01)
        sessions = browser.BrowserSessions()
        with pytest.raises(browser.BrowserSessionError, match="TimeoutError"):
            await sessions.open("run", tmp_path)
        assert driver.stopped == 1

    asyncio.run(exercise())


def test_video_finalization_failure_retains_clips_for_retry(fake, tmp_path):
    async def exercise():
        sessions = browser.BrowserSessions()
        await sessions.open("run", tmp_path, record_video=True)
        video = fake.drivers[0].context.pages[0].video
        video.fail = True
        with pytest.raises(ApplicationError, match="Video finalization failed"):
            await sessions.close("run")
        with pytest.raises(browser.BrowserSessionError, match="collect its videos"):
            await sessions.open("run", tmp_path)
        video.fail = False
        assert await sessions.close("run") == [video.file]

    asyncio.run(exercise())


def test_close_all_finishes_other_sessions_and_preserves_recordings(fake, tmp_path):
    async def exercise():
        sessions = browser.BrowserSessions()
        await sessions.open("bad", tmp_path)
        await sessions.open("recorded", tmp_path, record_video=True)
        fake.drivers[0].stop_error = True
        with pytest.raises(ApplicationError):
            await sessions.close_all()
        assert fake.drivers[1].stopped == 1
        assert len(await sessions.close("recorded")) == 1
        fake.drivers[0].stop_error = False
        await sessions.close_all()

    asyncio.run(exercise())


def test_recording_disk_limit_stops_browser(fake, tmp_path, monkeypatch):
    async def exercise():
        monkeypatch.setattr(browser, "MAX_RECORDING_BYTES", 1)
        sessions = browser.BrowserSessions()
        await sessions.open("run", tmp_path, record_video=True)
        fake.drivers[0].context.pages[0].video.file.write_bytes(b"recording")
        await asyncio.sleep(1.1)
        assert not sessions.info("run").active
        assert "Recording limit" in sessions.info("run").problem
        assert len(await sessions.close("run")) == 1

    asyncio.run(exercise())


def test_real_chromium_cdp_recording_and_capture_when_available(tmp_path):
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("optional Playwright is not installed")

    async def exercise():
        from playwright.async_api import async_playwright

        async with async_playwright() as probe:
            if not Path(probe.chromium.executable_path).is_file():
                pytest.skip("optional Chromium is not installed; no automatic download")
        sessions = browser.BrowserSessions()
        try:
            info = await sessions.open("real", tmp_path, record_video=True)
            async with async_playwright() as client:
                remote = await client.chromium.connect_over_cdp(info.cdp_url)
                page = await remote.contexts[0].new_page()
                await page.goto("about:blank")
                await page.set_content("<h1>Same owned browser, external CDP tab</h1>")
                assert (await sessions.frame("real")).startswith(b"\xff\xd8")
                path = await sessions.screenshot("real", tmp_path / "shot.png")
                assert path.read_bytes().startswith(b"\x89PNG")
                await page.close()
                assert (await sessions.frame("real")).startswith(b"\xff\xd8")
                # Disconnect the CDP client; this must not close the owned context.
            assert sessions.info("real").active
            videos = await sessions.close("real")
            assert len(videos) >= 2 and all(path.stat().st_size > 0 for path in videos)
        finally:
            await sessions.close_all()

    asyncio.run(exercise())
