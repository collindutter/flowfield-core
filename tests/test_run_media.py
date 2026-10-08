"""Run-scoped media tools and local HTTP views, without a browser or model."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_execution import BASE, fixture

from flowfield.adapters.browser_session import BrowserInfo
from flowfield.api import create_app
from flowfield.artifacts import Artifacts
from flowfield.errors import ApplicationError
from flowfield.execution_models import QueueEdit, RunAction
from flowfield.run_media import AttemptMedia
from flowfield.worker_tools import WorkerBridge

PNG = b"\x89PNG\r\n\x1a\n" + b"fixture"
WEBM = b"\x1a\x45\xdf\xa3\x42\x82\x84webm" + b"fixture"


class Browser:
    def __init__(self):
        self.active = False
        self.recording = False
        self.runtime = None
        self.problem = None
        self.fail_close = False

    def info(self, run_id):
        return BrowserInfo(
            active=self.active,
            recording=self.recording,
            url="https://example.test" if self.active else None,
            problem=self.problem,
            cdp_url="http://127.0.0.1:9222" if self.active else None,
        )

    async def open(self, run_id, runtime, url="about:blank", record_video=False):
        self.active = True
        self.recording = record_video
        self.runtime = runtime
        return self.info(run_id)

    async def screenshot(self, run_id, path):
        path.write_bytes(PNG)
        return path

    async def frame(self, run_id):
        if not self.active:
            raise ApplicationError("browser_inactive", "No browser is running.", 409)
        return b"\xff\xd8\xfffixture"

    async def close(self, run_id):
        if self.fail_close:
            raise ApplicationError("browser_cleanup_uncertain", "Browser cleanup failed.", 409)
        self.active = False
        if not self.recording:
            return []
        self.recording = False
        video = self.runtime / "output" / "recording.webm"
        video.write_bytes(WEBM)
        return [video]

    async def close_all(self):
        self.active = False


def media_fixture(tmp_path):
    execution = fixture(tmp_path)
    run = execution.claim("harbor", BASE, {BASE: set()})
    assert run
    execution.started("harbor", run.id)
    checkout, runtime = tmp_path / "checkout", tmp_path / "runtime"
    checkout.mkdir()
    (runtime / "output").mkdir(parents=True)
    environment = SimpleNamespace(checkout=checkout, runtime=runtime)
    browser = Browser()
    media = AttemptMedia(execution, run, environment, browser)
    return execution, run, environment, browser, media


def test_worker_publishes_artifacts_and_finalizes_recording(tmp_path):
    execution, run, environment, browser, media = media_fixture(tmp_path)
    (environment.checkout / "report.txt").write_text("Observed results")
    bridge = WorkerBridge(execution, run, object(), media=media)

    async def exercise():
        report = json.loads(
            await bridge.call("publish_artifact", {"path": "report.txt", "title": "Report"})
        )
        assert report["run_id"] == run.id
        opened = json.loads(
            await bridge.call("browser_open", {"url": "https://example.test", "record_video": True})
        )
        assert opened["cdp_url"] == "http://127.0.0.1:9222"
        assert execution.local(run.id)["browser_cleanup_confirmed"] is False
        screenshot = json.loads(await bridge.call("browser_screenshot", {"title": "Result"}))
        assert screenshot["mime"] == "image/png"
        await bridge.call(
            "submit_result", {"outcome": "complete", "summary": "Done", "checks": "Observed"}
        )
        with pytest.raises(ApplicationError, match="report is saved"):
            await bridge.call("publish_artifact", {"path": "report.txt", "title": "Late"})
        videos = await media.close()
        assert len(videos) == 1 and videos[0].mime == "video/webm"
        assert execution.local(run.id)["browser_cleanup_confirmed"] is True
        assert await media.close() == []

    asyncio.run(exercise())
    assert len(Artifacts(execution.workspace).list("harbor", run_id=run.id)) == 3
    assert not browser.active


def test_stop_revokes_agent_media_but_allows_owned_recording_finalization(tmp_path):
    execution, run, _, _, media = media_fixture(tmp_path)
    bridge = WorkerBridge(execution, run, object(), media=media)

    async def exercise():
        await bridge.call("browser_open", {"record_video": True})
        current = execution.get("harbor", run.id)
        execution.stop_requested("harbor", run.id, RunAction(expected_revision=current.revision))
        with pytest.raises(ApplicationError):
            await bridge.call("browser_screenshot", {"title": "Late"})
        assert len(await media.close()) == 1

    asyncio.run(exercise())


def test_browser_cleanup_receipt_is_not_written_on_failure(tmp_path):
    execution, run, _, browser, media = media_fixture(tmp_path)

    async def exercise():
        await media.open("about:blank", False)
        browser.fail_close = True
        with pytest.raises(ApplicationError, match="cleanup failed"):
            await media.close()
        assert execution.local(run.id)["browser_cleanup_confirmed"] is False
        browser.fail_close = False
        await media.close()
        assert execution.local(run.id)["browser_cleanup_confirmed"] is True

    asyncio.run(exercise())


def test_media_http_is_scoped_read_only_and_hides_control_endpoint(tmp_path):
    execution = fixture(tmp_path)
    app = create_app(data_dir=execution.workspace.directory)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        service = app.state.supervisor
        # Disable scheduling; this attempt is exercised directly.
        service.closing = True
        settings = service.execution.settings("harbor")
        service.execution.queue(
            "harbor", QueueEdit(expected_revision=settings.revision, enabled=True)
        )
        run = service.execution.claim("harbor", BASE, {BASE: set()})
        assert run
        service.execution.started("harbor", run.id)
        browser = Browser()
        browser.active = True
        service.browsers = browser
        base = f"/api/projects/harbor/runs/{run.id}/browser"
        response = client.get(base)
        assert response.status_code == 200
        assert response.json()["active"] is True
        assert "cdp_url" not in response.json()
        assert client.get(base + "/frame").headers["content-type"] == "image/jpeg"
        assert client.get(base.replace("harbor", "elsewhere")).status_code == 404
        assert (
            client.get(base + "/frame", headers={"Origin": "https://evil.test"}).status_code == 403
        )
        assert client.post(base, json={"url": "https://example.test"}).status_code == 404
        browser.active = False
        assert client.get(base + "/frame").status_code == 409
        service.execution.finish("harbor", run.id, "stopped")
