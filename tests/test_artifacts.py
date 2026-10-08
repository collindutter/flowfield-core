"""Attempt-owned output survives worktree removal and is read only through scoped IDs."""

import base64
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from test_execution import BASE, fixture

from flowfield import artifacts as module
from flowfield.application import ProjectSetup, Workspace
from flowfield.artifact_api import artifact_router
from flowfield.artifacts import MAX_FILE, Artifacts
from flowfield.errors import ApplicationError

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII="
)
MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isommp42" + b"video contents"
WEBM = b"\x1a\x45\xdf\xa3\x87\x42\x82\x84webm" + b"video contents"


def setup(tmp_path, *, count=1):
    execution = fixture(tmp_path, count=count, cap=count)
    run = execution.claim("harbor", BASE, {BASE: set()})
    execution.started("harbor", run.id)
    source = tmp_path / "output"
    source.mkdir()
    return execution, run, source, Artifacts(execution.workspace)


def publish(files, run, source, *, name="screen.png", content=PNG, **kwargs):
    path = source / name
    path.write_bytes(content)
    return files.publish("harbor", run.id, path, roots=(source,), title="Evidence", **kwargs)


def client_for(workspace):
    app = FastAPI()

    @app.exception_handler(ApplicationError)
    async def application_error(request: Request, error: ApplicationError):
        return JSONResponse({"error": error.code}, status_code=error.status)

    app.include_router(artifact_router(lambda: workspace))
    return TestClient(app)


def test_copy_is_immutable_and_survives_source_deletion_and_closed_run(tmp_path):
    execution, run, source, files = setup(tmp_path)
    artifact = publish(files, run, source, description="Screenshot")
    assert artifact.task_id == run.task_id
    assert artifact.name == "screen.png" and artifact.size == len(PNG)
    assert artifact.description == "Screenshot" and artifact.mime == "image/png"
    stored = files.stored_path(artifact)
    assert stored.parent == execution.workspace.directory / "artifacts"
    assert stored.stat().st_ino != (source / artifact.name).stat().st_ino
    assert stored.stat().st_mode & 0o222 == 0
    (source / artifact.name).write_bytes(b"changed")
    (source / artifact.name).unlink()
    source.rmdir()
    execution.finish("harbor", run.id, "stopped")
    reopened = Artifacts(Workspace(execution.workspace.directory))
    assert reopened.list("harbor", run_id=run.id) == [artifact]
    assert reopened.list("harbor", task_id=run.task_id) == [artifact]
    with client_for(reopened.workspace) as client:
        assert client.get(artifact.href).content == PNG
        assert client.get(f"/api/projects/harbor/runs/{run.id}/artifacts").json() == [
            artifact.model_dump()
        ]
        assert client.get(f"/api/projects/harbor/tasks/{run.task_key}/artifacts").json() == [
            artifact.model_dump()
        ]


def test_scoping_and_publish_attempt_status(tmp_path):
    execution, run, source, files = setup(tmp_path, count=2)
    other = execution.claim("harbor", BASE, {BASE: set()})
    artifact = publish(files, run, source)
    assert files.list("harbor", run_id=other.id) == []
    assert files.list("harbor", task_id=other.task_id) == []
    with pytest.raises(ApplicationError):
        files.list("harbor", run_id=run.id, task_id=other.task_id)
    for method in (
        lambda: files.list("elsewhere", run_id=run.id),
        lambda: files.list("harbor", run_id="missing"),
        lambda: files.list("harbor", task_id="missing"),
        lambda: files.get("elsewhere", artifact.id),
        lambda: files.publish(
            "elsewhere", run.id, source / artifact.name, roots=(source,), title="X"
        ),
    ):
        with pytest.raises(ApplicationError):
            method()
    with execution.workspace.connection(write=True) as db:
        db.execute("UPDATE runs SET status='stopping' WHERE id=?", (run.id,))
    assert publish(files, run, source).run_id == run.id
    with execution.workspace.connection(write=True) as db:
        db.execute("UPDATE runs SET status='uncertain' WHERE id=?", (run.id,))
    with pytest.raises(ApplicationError, match="active attempt"):
        publish(files, run, source)
    other_project = tmp_path / "other"
    other_project.mkdir()
    execution.workspace.setup_project(
        ProjectSetup(path=str(other_project), id="other", name="Other")
    )
    with pytest.raises(ApplicationError, match="Attempt not found"):
        files.publish("other", run.id, source / artifact.name, roots=(source,), title="X")
    with pytest.raises(ApplicationError, match="Attempt not found"):
        files.list("other", run_id=run.id)
    with client_for(execution.workspace) as client:
        assert client.get(artifact.href.replace("harbor", "other")).status_code == 404
        assert client.get(artifact.href.replace("harbor", "elsewhere")).status_code == 404
        assert client.get("/api/projects/harbor/artifacts/unknown").status_code == 404
        assert (
            client.get("/api/projects/harbor/artifacts/..%2Fworkspace.sqlite3").status_code == 404
        )
        assert client.post(f"/api/projects/harbor/runs/{run.id}/artifacts").status_code == 405


@pytest.mark.parametrize(
    "kind",
    [
        "outside",
        "escape",
        "symlink",
        "intermediate",
        "root",
        "fifo",
        "directory",
        "oversize",
        "empty-roots",
    ],
)
def test_rejects_unsafe_paths_without_storing_anything(tmp_path, kind):
    _, run, source, files = setup(tmp_path)
    path = source / "file.txt"
    path.write_text("evidence")
    roots = (source,)
    if kind == "outside":
        path = tmp_path / "outside.txt"
        path.write_text("outside")
    elif kind == "escape":
        path = source / ".." / "output" / "file.txt"
    elif kind == "symlink":
        path = source / "link.txt"
        path.symlink_to(source / "file.txt")
    elif kind in {"intermediate", "root"}:
        link = tmp_path / "link"
        link.symlink_to(source, target_is_directory=True)
        path = link / "file.txt"
        roots = (tmp_path,) if kind == "intermediate" else (link,)
    elif kind == "fifo":
        path.unlink()
        os.mkfifo(path)
    elif kind == "directory":
        path = source
    elif kind == "oversize":
        with path.open("wb") as stream:
            stream.truncate(MAX_FILE + 1)
    elif kind == "empty-roots":
        roots = ()
    with pytest.raises(ApplicationError):
        files.publish("harbor", run.id, path, roots=roots, title="Evidence")
    assert files.list("harbor") == []
    assert list((files.workspace.directory / "artifacts").iterdir()) == []


@pytest.mark.parametrize(
    "name,content,mime",
    [
        ("image.jpg", b"\xff\xd8\xffdata", "image/jpeg"),
        ("image.webp", b"RIFF\x04\x00\x00\x00WEBP", "image/webp"),
        ("video.webm", WEBM, "video/webm"),
        ("video.mp4", MP4, "video/mp4"),
        ("notes.txt", "Unicode ✓".encode(), "text/plain"),
        ("notes.txt", b"", "text/plain"),
        ("file.bin", b"\x00\xff", "application/octet-stream"),
        ("page.html", b"<html>unsafe</html>", "application/octet-stream"),
        ("image.svg", b"<svg/>", "application/octet-stream"),
        ("fake.txt", b"<!doctype html><html/>", "application/octet-stream"),
    ],
)
def test_mime_detection_and_safe_disposition(tmp_path, name, content, mime):
    _, run, source, files = setup(tmp_path)
    artifact = publish(files, run, source, name=name, content=content)
    assert artifact.mime == mime
    with client_for(files.workspace) as client:
        response = client.get(artifact.href)
        assert response.content == content
        assert response.headers["content-type"].startswith(mime)
        disposition = "attachment" if mime == "application/octet-stream" else "inline"
        assert response.headers["content-disposition"].startswith(disposition)
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "sandbox" in response.headers["content-security-policy"]
        download = client.get(artifact.href, params={"download": "true"})
        assert download.content == content
        assert download.headers["content-disposition"].startswith("attachment")


@pytest.mark.parametrize(
    "name,content",
    [
        ("fake.png", b"not png"),
        ("fake.jpg", b"not jpeg"),
        ("fake.webp", b"RIFF"),
        ("fake.webm", b"\x1a\x45\xdf\xa3matroska"),
        ("fake.mp4", b"\x00\x00\x00\x18ftyp"),
        ("bad.txt", b"\xff"),
        ("bad.txt", b"null\x00"),
        ("bad\nname.txt", b"text"),
    ],
)
def test_invalid_media_text_and_names(tmp_path, name, content):
    _, run, source, files = setup(tmp_path)
    with pytest.raises(ApplicationError):
        publish(files, run, source, name=name, content=content)
    assert files.list("harbor") == []
    assert list((files.workspace.directory / "artifacts").iterdir()) == []


@pytest.mark.parametrize("name,content", [("video.mp4", MP4), ("video.webm", WEBM)])
def test_video_byte_ranges(tmp_path, name, content):
    _, run, source, files = setup(tmp_path)
    artifact = publish(files, run, source, name=name, content=content)
    with client_for(files.workspace) as client:
        response = client.get(artifact.href, headers={"Range": "bytes=4-11"})
        assert response.status_code == 206 and response.content == content[4:12]
        assert response.headers["content-range"] == f"bytes 4-11/{len(content)}"
        assert response.headers["accept-ranges"] == "bytes"
        assert client.get(artifact.href, headers={"Range": "bytes=-4"}).content == content[-4:]
        assert client.get(artifact.href, headers={"Range": "bytes=999-"}).status_code == 416


def test_quota_count_and_concurrent_publish_are_bounded(tmp_path, monkeypatch):
    _, run, source, files = setup(tmp_path)
    monkeypatch.setattr(module, "MAX_PROJECT", len(PNG))
    first = publish(files, run, source)
    with pytest.raises(ApplicationError, match="storage is full"):
        publish(files, run, source)
    monkeypatch.setattr(module, "MAX_PROJECT", 1024)
    monkeypatch.setattr(module, "MAX_ARTIFACTS", 2)
    path = source / "screen.png"

    def attempt(_):
        try:
            return files.publish("harbor", run.id, path, roots=(source,), title="X")
        except ApplicationError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(attempt, range(2)))
    assert sum(isinstance(value, ApplicationError) for value in values) == 1
    assert len(files.list("harbor")) == 2 and first in files.list("harbor")
    assert len(list((files.workspace.directory / "artifacts").iterdir())) == 2


def test_database_failure_cleans_up_atomic_copy(tmp_path):
    _, run, source, files = setup(tmp_path)
    with files.workspace.connection(write=True) as db:
        db.execute(
            "CREATE TRIGGER reject_artifact BEFORE INSERT ON artifacts "
            "BEGIN SELECT RAISE(ABORT, 'injected'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        publish(files, run, source)
    assert files.list("harbor") == []
    assert list((files.workspace.directory / "artifacts").iterdir()) == []


def test_source_change_during_copy_is_rejected(tmp_path, monkeypatch):
    _, run, source, files = setup(tmp_path)
    path = source / "file.txt"
    path.write_text("original")
    original = module._fingerprint
    changed = False

    def fingerprint(info):
        nonlocal changed
        if not changed:
            changed = True
            path.write_text("replaced")
        return original(info)

    monkeypatch.setattr(module, "_fingerprint", fingerprint)
    with pytest.raises(ApplicationError, match="changed"):
        files.publish("harbor", run.id, path, roots=(source,), title="X")
    assert files.list("harbor") == []
    assert list((files.workspace.directory / "artifacts").iterdir()) == []


def test_media_symlink_and_missing_media_are_not_downloaded(tmp_path):
    _, run, source, files = setup(tmp_path)
    artifact = publish(files, run, source)
    stored = files.stored_path(artifact)
    stored.unlink()
    with client_for(files.workspace) as client:
        assert client.get(artifact.href).status_code == 404
        stored.symlink_to(source / artifact.name)
        assert client.get(artifact.href).status_code == 404


def test_workspace_rejects_artifact_directory_symlink(tmp_path):
    workspace = Workspace(tmp_path / "state")
    directory = workspace.directory / "artifacts"
    directory.rmdir()
    directory.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ApplicationError, match="symlink"):
        Workspace(workspace.directory)


def test_transaction_failure_cleans_up_published_copy(tmp_path, monkeypatch):
    _, run, source, files = setup(tmp_path)
    connection = files.workspace.connection

    @contextmanager
    def fail_commit(**kwargs):
        with connection(**kwargs) as db:
            yield db
            raise RuntimeError("injected transaction failure")

    with monkeypatch.context() as patch:
        patch.setattr(files.workspace, "connection", fail_commit)
        with pytest.raises(RuntimeError, match="transaction failure"):
            publish(files, run, source)
    assert files.list("harbor") == []
    assert list((files.workspace.directory / "artifacts").iterdir()) == []
