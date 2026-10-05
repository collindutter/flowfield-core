import fcntl
import hashlib
import json
import stat
import zipfile

import httpx
import pytest
from typer.testing import CliRunner

from flowfield import __version__
from flowfield.adapters import codex_install as bridge
from flowfield.cli import app
from flowfield.errors import ApplicationError


def bundle(tmp_path, *, change=None, extra=None, binary=b"standalone test executable"):
    contents = {
        bridge.EXECUTABLE: binary,
        "LICENSE": b"license",
        "THIRD_PARTY_NOTICES.txt": b"notices",
    }
    metadata = {
        "schema": 1,
        "name": bridge.EXECUTABLE,
        **bridge.SPEC,
        "platform": bridge.target(),
        "files": {name: hashlib.sha256(value).hexdigest() for name, value in contents.items()},
    }
    if change:
        change(metadata)
    contents["manifest.json"] = json.dumps(metadata).encode()
    if extra:
        contents.update(extra)
    path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as output:
        for name, value in contents.items():
            output.writestr(name, value)
    return path, bridge.digest(path)


def test_install_is_offline_idempotent_and_preserves_native_environment(tmp_path):
    package, checksum = bundle(tmp_path)
    state = tmp_path / "state"
    binary = bridge.install(state, package, checksum)
    assert bridge.install(state, package, checksum) == binary
    assert bridge.installed(state) == binary
    native = tmp_path / "codex"
    native.write_text("#!/bin/sh\nexit 0\n")
    native.chmod(0o755)
    environment = {
        "PATH": str(tmp_path),
        "HOME": "native-home",
        "TOKEN": "secret",
        "APP_SERVER_LOGS": "private",
    }
    command, launch = bridge.command(state, environment)
    assert command == [str(binary)]
    assert launch == {
        "PATH": str(tmp_path),
        "HOME": "native-home",
        "TOKEN": "secret",
        "CODEX_PATH": str(native),
    }
    assert environment["APP_SERVER_LOGS"] == "private"
    assert not (state / "workspace.sqlite3").exists()
    assert not list(state.rglob(".install-*"))
    assert bridge.download_install(state) == binary  # An installed version needs no network.


@pytest.mark.parametrize(
    "change",
    [
        lambda m: m.update(version="different"),
        lambda m: m.update(platform="other-os"),
        lambda m: m.update(schema=True),
        lambda m: m.update(unexpected="field"),
        lambda m: m["files"].update({bridge.EXECUTABLE: "0" * 64}),
    ],
)
def test_incompatible_bundle_preserves_previous_installation(tmp_path, change):
    package, checksum = bundle(tmp_path)
    state = tmp_path / "state"
    previous = bridge.install(state, package, checksum)
    package, checksum = bundle(tmp_path, change=change)
    with pytest.raises(ApplicationError):
        bridge.install(state, package, checksum)
    assert bridge.installed(state) == previous


def test_checksum_traversal_symlink_and_size_rejections(tmp_path, monkeypatch):
    package, checksum = bundle(tmp_path)
    with pytest.raises(ApplicationError, match="checksum mismatch"):
        bridge.install(tmp_path / "state", package, "0" * 64)
    package, checksum = bundle(tmp_path, extra={"../escape": b"oops"})
    with pytest.raises(ApplicationError, match="Unexpected files"):
        bridge.install(tmp_path / "state", package, checksum)
    assert not (tmp_path / "escape").exists()
    package, checksum = bundle(tmp_path)
    with zipfile.ZipFile(package, "a") as output:
        entry = zipfile.ZipInfo("linked")
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        output.writestr(entry, "outside")
    with pytest.raises(ApplicationError):
        bridge.install(tmp_path / "state", package, bridge.digest(package))
    package, checksum = bundle(tmp_path)
    monkeypatch.setattr(bridge, "MAX_UNPACKED", 10)
    with pytest.raises(ApplicationError, match="size"):
        bridge.install(tmp_path / "state", package, checksum)
    monkeypatch.setattr(bridge, "MAX_ARCHIVE", 10)
    with pytest.raises(ApplicationError, match="too large"):
        bridge.install(tmp_path / "state", package, checksum)


def test_damaged_install_repair_does_not_replace_a_previous_executable_path(tmp_path):
    package, checksum = bundle(tmp_path)
    state = tmp_path / "state"
    previous = bridge.install(state, package, checksum)
    previous.write_text("damaged")
    with pytest.raises(ApplicationError, match="checksums"):
        bridge.installed(state)
    repaired = bridge.install(state, package, checksum)
    assert repaired != previous and previous.read_text() == "damaged"
    assert bridge.installed(state) == repaired
    package, checksum = bundle(tmp_path, binary=b"another verified build")
    upgraded = bridge.install(state, package, checksum)
    assert upgraded != repaired and repaired.is_file()


def test_concurrent_install_has_one_owner(tmp_path):
    package, checksum = bundle(tmp_path)
    state = tmp_path / "state"
    bridge.install(state, package, checksum)
    root = bridge.installed(state).parent.parent
    with (root / "install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ApplicationError, match="in progress"):
            bridge.install(state, package, checksum)


def test_interrupted_selection_keeps_previous_install(tmp_path, monkeypatch):
    package, checksum = bundle(tmp_path)
    state = tmp_path / "state"
    previous = bridge.install(state, package, checksum)
    package, checksum = bundle(tmp_path, binary=b"next verified executable")

    def interrupted(*args):
        raise OSError("interrupted before atomic replacement")

    monkeypatch.setattr(bridge.os, "replace", interrupted)
    with pytest.raises(OSError):
        bridge.install(state, package, checksum)
    assert bridge.installed(state) == previous
    assert not list(state.rglob(".install-*"))


def test_missing_native_and_explicit_codex_path(tmp_path):
    package, checksum = bundle(tmp_path)
    state = tmp_path / "state"
    bridge.install(state, package, checksum)
    assert not bridge.status(state, {"PATH": ""})["codex_available"]
    with pytest.raises(ApplicationError, match="service PATH"):
        bridge.command(state, {"PATH": ""})
    native = tmp_path / "custom-codex"
    native.write_text("native")
    native.chmod(0o755)
    assert bridge.status(state, {"PATH": "", "CODEX_PATH": str(native)})["codex_available"]


@pytest.mark.parametrize(
    "failure", [None, "digest", "url", "missing", "draft", "malformed", "oversized"]
)
def test_download_is_pinned_to_own_release_and_verified(tmp_path, monkeypatch, failure):
    package, checksum = bundle(tmp_path)
    name = f"{bridge.EXECUTABLE}-{bridge.VERSION}-{bridge.target()}.zip"
    url = f"https://github.com/flowfield-sh/flowfield-core/releases/download/v{__version__}/{name}"
    metadata = {
        "tag_name": "v" + __version__,
        "draft": failure == "draft",
        "assets": [
            {
                "name": name,
                "browser_download_url": "https://unrelated.invalid/a" if failure == "url" else url,
                "digest": None if failure == "digest" else "sha256:" + checksum,
            }
        ],
    }
    calls = []

    def respond(request):
        calls.append(str(request.url))
        if str(request.url) == bridge.RELEASES + "v" + __version__:
            if failure == "missing":
                return httpx.Response(404)
            if failure == "oversized":
                return httpx.Response(200, content=b"x" * (1024 * 1024 + 1))
            return httpx.Response(200, json=[] if failure == "malformed" else metadata)
        assert str(request.url) == url
        return httpx.Response(200, content=package.read_bytes())

    original = httpx.Client
    monkeypatch.setattr(
        bridge.httpx,
        "Client",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(respond)),
    )
    if failure:
        with pytest.raises(ApplicationError):
            bridge.download_install(tmp_path / "state")
        assert len(calls) == 1
    else:
        assert bridge.download_install(tmp_path / "state").is_file()
        assert calls == [bridge.RELEASES + "v" + __version__, url]


def test_cli_install_and_status_do_not_start_service_or_agent(tmp_path, monkeypatch):
    package, checksum = bundle(tmp_path)
    monkeypatch.setenv("PATH", "")
    runner = CliRunner()
    prefix = ["--data-dir", str(tmp_path / "state"), "harness"]
    result = runner.invoke(
        app, [*prefix, "install", "codex", "--bundle", str(package), "--sha256", checksum, "--json"]
    )
    assert result.exit_code == 0, result.output
    assert not json.loads(result.stdout)["codex_available"]
    result = runner.invoke(app, [*prefix, "status", "codex", "--json"])
    assert result.exit_code == 0 and json.loads(result.stdout)["version"] == bridge.VERSION
    result = runner.invoke(app, [*prefix, "install", "codex", "--bundle", str(package), "--json"])
    assert result.exit_code == 1 and "together" in result.stderr
    result = runner.invoke(app, [*prefix, "install", "unknown", "--json"])
    assert result.exit_code == 1 and "unsupported_harness" in result.stderr
