"""Native selection is explicit, bounded and separate from project/worker setup."""

import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from flowfield.adapters import directory_picker
from flowfield.api import create_app
from flowfield.application import ProjectSetup, Workspace
from flowfield.errors import ApplicationError
from flowfield.integration import Integrations
from flowfield.integration_models import LocalAdoption


def test_picker_selection_cancel_and_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "Project with spaces 🚢"
    root.mkdir()
    monkeypatch.setattr(directory_picker.sys, "platform", "darwin")
    run = Mock(return_value=subprocess.CompletedProcess([], 0, str(root) + "/\n", ""))
    monkeypatch.setattr(directory_picker.subprocess, "run", run)
    with TestClient(create_app(data_dir=tmp_path / "state"), base_url="http://localhost") as client:
        assert (
            client.post(
                "/api/projects/select-directory", headers={"origin": "https://elsewhere.example"}
            ).status_code
            == 403
        )
        run.assert_not_called()
        selected = client.post("/api/projects/select-directory")
        assert selected.json() == {"path": str(root)}
        assert client.get("/api/projects").json() == []
        assert not (root / ".flowfield").exists()
        assert run.call_args.kwargs["timeout"] == directory_picker.TIMEOUT
        run.return_value = subprocess.CompletedProcess([], 0, "\n", "")
        assert client.post("/api/projects/select-directory").json() == {"path": None}
        assert client.get("/api/projects").json() == []


def test_picker_failure_timeout_busy_and_missing_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(directory_picker, "picker_command", lambda: ["fake-picker"])
    run = Mock(side_effect=subprocess.TimeoutExpired("fake-picker", 300))
    monkeypatch.setattr(directory_picker.subprocess, "run", run)
    with pytest.raises(ApplicationError, match="timed out"):
        directory_picker.select_directory()
    run.side_effect = OSError("missing executable")
    with pytest.raises(ApplicationError, match="project init"):
        directory_picker.select_directory()
    run.side_effect = None
    run.return_value = subprocess.CompletedProcess([], 2, "", "private diagnostic")
    with pytest.raises(ApplicationError, match="Could not select"):
        directory_picker.select_directory()
    run.return_value = subprocess.CompletedProcess([], 0, str(tmp_path / "missing"), "")
    with pytest.raises(ApplicationError, match="existing project"):
        directory_picker.select_directory()
    assert directory_picker._selection.acquire(blocking=False)
    try:
        with pytest.raises(ApplicationError, match="already open"):
            directory_picker.select_directory()
    finally:
        directory_picker._selection.release()


def test_desktop_commands_and_headless_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(directory_picker.sys, "platform", "win32")
    assert "-STA" in directory_picker.picker_command()
    monkeypatch.setattr(directory_picker.sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":1")
    monkeypatch.setattr(
        directory_picker.shutil, "which", lambda name: name if name == "zenity" else None
    )
    assert directory_picker.picker_command()[:3] == ["zenity", "--file-selection", "--directory"]
    monkeypatch.setattr(
        directory_picker.shutil, "which", lambda name: name if name == "kdialog" else None
    )
    assert directory_picker.picker_command()[:2] == ["kdialog", "--getexistingdirectory"]
    monkeypatch.setattr(
        directory_picker.subprocess,
        "run",
        Mock(return_value=subprocess.CompletedProcess([], 1, "", "")),
    )
    assert directory_picker.select_directory().path is None
    monkeypatch.delenv("DISPLAY")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    with pytest.raises(ApplicationError, match="project init"):
        directory_picker.picker_command()


def test_local_adoption_before_git_or_delivery_and_revision_conflict(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    workspace = Workspace(tmp_path / "state")
    workspace.setup_project(ProjectSetup(path=str(root)))
    integration = Integrations(workspace)
    before = integration.settings("project")
    with pytest.raises(ApplicationError, match="Select Local"):
        before.require_local()
    selected = integration.adopt_local("project", LocalAdoption(expected_revision=1))
    selected.require_local()
    assert selected.revision == 2
    assert selected.model_dump(exclude={"revision", "runtime"}) == before.model_dump(
        exclude={"revision", "runtime"}
    )
    assert not (root / ".git").exists()
    assert integration.adopt_local("project", LocalAdoption(expected_revision=2)) == selected
    with pytest.raises(ApplicationError, match="stale"):
        integration.adopt_local("project", LocalAdoption(expected_revision=1))
    assert Integrations(Workspace(workspace.directory)).settings("project") == selected


def test_local_adoption_api(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    with TestClient(create_app(data_dir=tmp_path / "state"), base_url="http://localhost") as client:
        client.post("/api/projects/initialize", json={"path": str(root)})
        path = "/api/projects/project/integration/local"
        assert (
            client.post(
                path, json={"expected_revision": 1}, headers={"origin": "https://evil.example"}
            ).status_code
            == 403
        )
        assert client.get("/api/projects/project/integration").json()["runtime"] == "legacy"
        selected = client.post(path, json={"expected_revision": 1})
        assert selected.status_code == 200
        assert selected.json()["runtime"] == "local"
        assert selected.json()["checks"] == []
        assert selected.json()["target_branch"] is None
        assert client.post(path, json={"expected_revision": 1}).status_code == 409
