"""Explicit, versioned installation of the standalone Codex ACP executable.

No service/model launch, package-manager shell command, native Codex replacement or
automatic update. A running attempt keeps its immutable executable path.
"""

import hashlib
import json
import os
import platform
import re
import shutil
import stat
import tempfile
import time
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from flowfield import __version__
from flowfield.errors import ApplicationError

SPEC = json.loads(Path(__file__).with_name("codex_bridge.json").read_text())
VERSION: str = SPEC["version"]
EXECUTABLE = "flowfield-codex-acp"
FILES = {EXECUTABLE, "LICENSE", "THIRD_PARTY_NOTICES.txt"}
MAX_ARCHIVE = 128 * 1024 * 1024
MAX_UNPACKED = 256 * 1024 * 1024
RELEASES = "https://api.github.com/repos/flowfield-sh/flowfield-core/releases/tags/"


def target() -> str:
    system = {"Darwin": "darwin", "Linux": "linux"}.get(platform.system())
    machine = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64"}.get(platform.machine())
    if not system or not machine:
        raise ApplicationError(
            "unsupported_platform", "Managed agents require macOS or Linux on arm64/x64."
        )
    return f"{system}-{machine}"


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def invalid(message: str) -> ApplicationError:
    return ApplicationError("invalid_bridge_bundle", message, 409)


def manifest(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 16384:
        raise invalid("Bridge manifest is too large.")
    try:
        value = json.loads(path.read_text())
        if (
            not isinstance(value, dict)
            or set(value)
            != {
                "schema",
                "name",
                "version",
                "upstream_revision",
                "bun_version",
                "platform",
                "files",
            }
            or type(value["schema"]) is not int
            or value["schema"] != 1
            or value["name"] != EXECUTABLE
            or value["platform"] != target()
            or any(value.get(key) != expected for key, expected in SPEC.items())
            or not isinstance(value["files"], dict)
            or set(value["files"]) != FILES
            or any(
                not isinstance(h, str) or re.fullmatch(r"[0-9a-f]{64}", h) is None
                for h in value["files"].values()
            )
        ):
            raise invalid("The bundle is incompatible with this Flowfield version or platform.")
        return value
    except (ValueError, UnicodeError) as error:
        raise invalid("Bridge manifest is not valid JSON.") from error


def verify(directory: Path) -> dict[str, Any]:
    if directory.is_symlink() or any(
        (directory / name).is_symlink() for name in FILES | {"manifest.json"}
    ):
        raise invalid("Bridge installation must contain regular files.")
    value = manifest(directory / "manifest.json")
    for name, expected in value["files"].items():
        path = directory / name
        if not path.is_file() or path.stat().st_size > MAX_UNPACKED or digest(path) != expected:
            raise invalid(
                "Bridge files do not match their recorded checksums. Reinstall the bundle."
            )
    return value


def installed(directory: Path) -> Path:
    root = directory / "harnesses/codex" / VERSION / target()
    try:
        pointer = root / "current"
        if pointer.stat().st_size > 100:
            raise invalid("Invalid bridge installation pointer.")
        current = pointer.read_text().strip()
        if re.fullmatch(r"[0-9a-f]{64}-[0-9a-f]{32}", current) is None:
            raise invalid("Invalid bridge installation pointer.")
        selected = root / current
        verify(selected)
        binary = selected / EXECUTABLE
        if not os.access(binary, os.X_OK):
            raise invalid("The installed bridge is not executable. Reinstall the bundle.")
        return binary
    except FileNotFoundError as error:
        raise ApplicationError(
            "bridge_missing",
            "Install the managed Codex runtime with `flowfield harness install codex`.",
            409,
        ) from error


def status(directory: Path, environment: Mapping[str, str]) -> dict[str, Any]:
    binary = installed(directory)
    codex = shutil.which(environment.get("CODEX_PATH") or "codex", path=environment.get("PATH", ""))
    return {
        "harness": "codex",
        "version": VERSION,
        "platform": target(),
        "executable": str(binary),
        "codex": codex,
        "codex_available": codex is not None,
        "available": codex is not None,
        "message": "Managed Codex runtime installed."
        if codex
        else "Bridge installed. Install Codex on the service PATH before starting managed work.",
    }


def command(directory: Path, environment: Mapping[str, str]) -> tuple[list[str], dict[str, str]]:
    """Resolve one immutable launch; native access policy remains harness-owned."""
    value = status(directory, environment)
    if not value["codex_available"]:
        raise ApplicationError("codex_missing", value["message"], 409)
    launch = dict(environment)
    launch["CODEX_PATH"] = str(Path(value["codex"]).resolve())
    # Bridge wire logging contains scoped MCP credentials. Native Codex settings
    # remain inherited; service-owned transport tokens must not enter debug files.
    launch.pop("APP_SERVER_LOGS", None)
    return [value["executable"]], launch


def install(directory: Path, bundle: Path, expected_sha256: str) -> Path:
    if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        raise invalid(
            "Provide the bundle's SHA-256 checksum (64 lowercase hexadecimal characters)."
        )
    root = directory / "harnesses/codex" / VERSION / target()
    import fcntl

    root.mkdir(parents=True, exist_ok=True)
    with (root / "install.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ApplicationError(
                "install_busy", "Another Codex installation is in progress.", 409
            ) from error
        with tempfile.TemporaryDirectory(prefix=".install-", dir=root) as temporary:
            scratch = Path(temporary)
            archive = scratch / "bundle.zip"
            checksum = hashlib.sha256()
            size = 0
            with bundle.open("rb") as source, archive.open("wb") as output:
                while block := source.read(1024 * 1024):
                    size += len(block)
                    if size > MAX_ARCHIVE:
                        raise invalid("Bridge archive is too large.")
                    checksum.update(block)
                    output.write(block)
            if checksum.hexdigest() != expected_sha256:
                raise invalid(
                    "Bridge archive checksum mismatch. The installed version was preserved."
                )
            candidate = scratch / "candidate"
            candidate.mkdir()
            try:
                with zipfile.ZipFile(archive) as zipped:
                    entries = zipped.infolist()
                    if (
                        len(entries) != len(FILES) + 1
                        or {e.filename for e in entries} != FILES | {"manifest.json"}
                        or sum(e.file_size for e in entries) > MAX_UNPACKED
                        or any(e.is_dir() or stat.S_ISLNK(e.external_attr >> 16) for e in entries)
                    ):
                        raise invalid("Unexpected files or size in the bridge archive.")
                    for entry in entries:
                        with (
                            zipped.open(entry) as source,
                            (candidate / entry.filename).open("wb") as output,
                        ):
                            shutil.copyfileobj(source, output, length=1024 * 1024)
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
                raise invalid("Invalid or unsupported bridge archive.") from error
            verify(candidate)
            (candidate / EXECUTABLE).chmod(0o755)
            try:
                previous = installed(directory).parent
                if previous.name.startswith(expected_sha256 + "-"):
                    return previous / EXECUTABLE
            except (ApplicationError, OSError):
                pass  # A fresh generation repairs damaged files without mutating old paths.
            destination = root / (expected_sha256 + "-" + uuid4().hex)
            candidate.rename(destination)
            pointer = scratch / "current"
            pointer.write_text(destination.name + "\n")
            os.replace(pointer, root / "current")
            return destination / EXECUTABLE


def download_install(directory: Path) -> Path:
    try:
        return installed(directory)
    except (ApplicationError, OSError):
        pass
    name = f"{EXECUTABLE}-{VERSION}-{target()}.zip"
    tag = "v" + __version__
    deadline = time.monotonic() + 120
    try:
        with httpx.Client(
            timeout=30, follow_redirects=True, headers={"User-Agent": "flowfield/" + __version__}
        ) as client:
            with client.stream("GET", RELEASES + tag) as response:
                if response.status_code == 404:
                    raise ApplicationError(
                        "bridge_unavailable",
                        "No Codex bundle is published for this Flowfield release. "
                        "Use --bundle PATH --sha256 CHECKSUM for a verified local build.",
                        409,
                    )
                response.raise_for_status()
                metadata = bytearray()
                for block in response.iter_bytes(16384):
                    metadata.extend(block)
                    if len(metadata) > 1024 * 1024 or time.monotonic() > deadline:
                        raise invalid("Release metadata exceeded its size or time limit.")
            release = json.loads(metadata)
            if not isinstance(release, dict) or not isinstance(release.get("assets"), list):
                raise invalid("Invalid release metadata.")
            assets = [
                item
                for item in release["assets"]
                if isinstance(item, dict) and item.get("name") == name
            ]
            if release.get("draft") or release.get("tag_name") != tag or len(assets) != 1:
                raise ApplicationError(
                    "bridge_unavailable",
                    "No compatible Codex bundle is published for this Flowfield "
                    "version and platform.",
                    409,
                )
            asset = assets[0]
            expected_url = (
                f"https://github.com/flowfield-sh/flowfield-core/releases/download/{tag}/{name}"
            )
            checksum = asset.get("digest", "")
            if (
                asset.get("browser_download_url") != expected_url
                or not isinstance(checksum, str)
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", checksum)
            ):
                raise invalid("Release asset has no verified source and SHA-256 digest.")
            with tempfile.TemporaryDirectory(prefix="flowfield-bridge-") as temporary:
                bundle = Path(temporary) / name
                size = 0
                with client.stream("GET", expected_url) as stream, bundle.open("wb") as output:
                    stream.raise_for_status()
                    for block in stream.iter_bytes(1024 * 1024):
                        size += len(block)
                        if size > MAX_ARCHIVE or time.monotonic() > deadline:
                            raise invalid("Bridge download exceeded its size or time limit.")
                        output.write(block)
                return install(directory, bundle, checksum.removeprefix("sha256:"))
    except (httpx.HTTPError, ValueError) as error:
        raise ApplicationError(
            "bridge_download_failed",
            "Cannot download the managed Codex bundle. "
            "Retry or use --bundle PATH --sha256 CHECKSUM.",
            409,
        ) from error
