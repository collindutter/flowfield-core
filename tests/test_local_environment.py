"""Immutable result collection must not execute project hooks/filters as the service."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from flowfield.adapters.local_environment import LocalEnvironment, baseline, git
from flowfield.adapters.toolchain import describe_environment, prepare_tools, readable_tools
from flowfield.environment_models import RESERVED_VARIABLES, EnvironmentConfig


def test_snapshot_preserves_source_and_collects_new_deleted_and_ignored(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init")
    (source / "old.txt").write_text("original")
    (source / ".gitignore").write_text("ignored.txt\n")
    git(source, "add", ".")
    git(
        source,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "base",
    )
    base = baseline(source)
    env = LocalEnvironment.prepare(tmp_path / "state", source, "attempt", base)
    marker = tmp_path / "filter-ran"
    git(source, "config", "filter.untrusted.clean", f"touch {marker}")
    (env.checkout / ".gitattributes").write_text("*.txt filter=untrusted\n")
    (env.checkout / "old.txt").unlink()
    (env.checkout / "new.txt").write_text("new content")
    (env.checkout / "ignored.txt").write_text("local artifact")
    result, ignored = env.snapshot(base)
    assert not marker.exists()
    assert ignored == ["ignored.txt"]
    assert baseline(source) == base and (source / "old.txt").read_text() == "original"
    assert git(source, "show", f"{result}:new.txt") == b"new content"
    assert git(source, "diff", "--name-status", base, result).decode().splitlines() == [
        "A\t.gitattributes",
        "A\tnew.txt",
        "D\told.txt",
    ]
    assert env.runtime.is_dir() and (env.checkout / "new.txt").exists()
    # Every fresh checkout can install Python packages without changing the host.
    result = subprocess.run(
        [str(env.runtime / "python/bin/python"), "-m", "pip", "--version"],
        capture_output=True,
        text=True,
        env=env.shell_environment(),
        check=True,
    )
    assert str(env.runtime / "python") in result.stdout
    assert Path(env.shell_environment()["npm_config_userconfig"]).parent == env.runtime
    assert str(env.runtime / "bin") in env.shell_environment()["PATH"]


def test_installed_node_package_entrypoints_are_exposed_without_user_shell(tmp_path, monkeypatch):
    prefix = tmp_path / "toolchain"
    package = prefix / "lib/node_modules/npm"
    (package / "bin").mkdir(parents=True)
    (package / "package.json").write_text('{"name":"npm"}')
    npm = package / "bin/npm-cli.js"
    npm.write_text("// installed tool")
    (prefix / "bin").mkdir()
    (prefix / "bin/npm").symlink_to(npm)
    node = prefix / "bin/node"
    node.write_text("installed node")
    unrelated = tmp_path / "personal-config"
    unrelated.write_text("not a tool")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    prepare_tools(
        runtime,
        EnvironmentConfig(tools={"node": str(node), "npm": str(npm)}, read_paths=[str(package)]),
    )
    assert (runtime / "bin/npm").resolve() == npm
    assert (runtime / "bin/node").resolve() == node
    roots = readable_tools(runtime)
    assert roots[str(package)] == "read" and roots[str(node)] == "read"
    assert str(tmp_path) not in roots and str(prefix) not in roots
    assert str(unrelated) not in roots


def test_environment_restrictions_are_discoverable_and_declared_tools_are_honest(tmp_path):
    help_text = EnvironmentConfig.model_json_schema()["properties"]["variables"]["description"]
    for name in RESERVED_VARIABLES:
        assert name in help_text
        with pytest.raises(ValidationError, match="reserved"):
            EnvironmentConfig(variables={name.lower(): "override"})
    for name in ("GIT_CONFIG", "DYLD_LIBRARY_PATH", "LD_PRELOAD", "CODEX_HOME", "https_proxy"):
        with pytest.raises(ValidationError, match="reserved"):
            EnvironmentConfig(variables={name: "override"})
    valid = EnvironmentConfig(variables={"APP_CACHE": "$RUNTIME/app"})
    assert valid.variables["APP_CACHE"] == "$RUNTIME/app"
    missing = str(tmp_path / "missing")
    info = describe_environment(EnvironmentConfig(tools={"sh": "/bin/sh", "rg": missing}))
    observed = {tool["name"]: tool["available_on_host"] for tool in info["configured_tools"]}
    assert observed == {"rg": False, "sh": True}


def test_uv_and_plain_python_share_each_private_environment(tmp_path):
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("uv is needed for this project environment regression")
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init")
    (source / "pyproject.toml").write_text(
        '[project]\nname="private-env-check"\nversion="0.1.0"\nrequires-python=">=3.12"\n'
    )
    git(source, "add", ".")
    git(
        source,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "baseline",
    )
    base = baseline(source)
    prefixes = []
    for identity in ("worker", "validation", "inspection"):
        env = LocalEnvironment.prepare(
            tmp_path / "state", source, identity, base, EnvironmentConfig(tools={"uv": uv})
        )
        command = ["uv", "run", "--offline", "python", "-c", "import sys; print(sys.prefix)"]
        result = subprocess.run(
            command,
            cwd=env.checkout,
            env=env.shell_environment(),
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
        )
        assert "does not match" not in result.stderr
        assert Path(result.stdout.strip()).resolve() == env.runtime / "python"
        assert not (env.checkout / ".venv").exists()
        # Ordinary Python resolves to the same environment too.
        prefix = subprocess.check_output(
            ["python", "-c", "import json,sys; print(json.dumps(sys.prefix))"],
            cwd=env.checkout,
            env=env.shell_environment(),
            text=True,
        )
        prefixes.append(json.loads(prefix))
    assert len(set(prefixes)) == 3
    assert not (source / ".venv").exists()
    assert baseline(source) == base
