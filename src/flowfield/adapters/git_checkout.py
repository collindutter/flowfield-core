"""Protected delivery into the registered checkout; no model or task policy."""

import shlex
import tempfile
from pathlib import Path

from flowfield.adapters.git_workspace import git
from flowfield.errors import ApplicationError
from flowfield.integration_models import CheckoutBinding


def binding(repository: Path) -> CheckoutBinding:
    root = Path(git(repository, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    if root != repository.resolve():
        raise ApplicationError(
            "checkout_unsupported", "Register the Git repository root for delivery.", 409
        )
    directory = Path(git(root, "rev-parse", "--absolute-git-dir").decode().strip()).resolve()
    info, git_info = root.stat(), directory.stat()
    return CheckoutBinding(
        path=str(root),
        git_dir=str(directory),
        device=info.st_dev,
        inode=info.st_ino,
        git_device=git_info.st_dev,
        git_inode=git_info.st_ino,
    )


def current_branch(repository: Path) -> str | None:
    return (
        git(repository, "symbolic-ref", "--quiet", "--short", "HEAD", allowed_returncodes=(0, 1))
        .decode()
        .strip()
        or None
    )


def verify(repository: Path, branch: str, commit: str, expected: CheckoutBinding) -> None:
    if binding(repository) != expected:
        raise ApplicationError(
            "delivery_destination_changed",
            "The project checkout was replaced or moved. Reconcile the destination "
            "before new approval.",
            409,
        )
    if current_branch(repository) != branch:
        raise ApplicationError(
            "checkout_branch_changed",
            f"Switch the project checkout to '{branch}', then retry integration. Local "
            "files are unchanged.",
            409,
        )
    if git(repository, "rev-parse", "HEAD").decode().strip() != commit:
        raise ApplicationError(
            "target_changed", "The destination moved. Prepare and approve a new candidate.", 409
        )
    directory = Path(expected.git_dir)
    for entry in git(repository, "worktree", "list", "--porcelain", "-z").split(b"\0\0"):
        fields = entry.split(b"\0")
        if (b"branch refs/heads/" + branch.encode()) in fields:
            location = next((field[9:] for field in fields if field.startswith(b"worktree ")), b"")
            if Path(location.decode()).resolve() != repository.resolve():
                raise ApplicationError(
                    "checkout_busy",
                    "The destination is also checked out in another worktree. "
                    "Resolve that checkout before retrying.",
                    409,
                )
    if any(
        (directory / item).exists()
        for item in (
            "MERGE_HEAD",
            "CHERRY_PICK_HEAD",
            "REVERT_HEAD",
            "rebase-merge",
            "rebase-apply",
            "sequencer",
            "BISECT_LOG",
            "index.lock",
        )
    ):
        raise ApplicationError(
            "checkout_busy",
            "Finish the current Git operation in the project, then retry integration.",
            409,
        )
    sparse = git(
        repository, "config", "--bool", "--get", "core.sparseCheckout", allowed_returncodes=(0, 1)
    ).strip()
    filters = git(
        repository, "config", "--get-regexp", r"^filter\.", allowed_returncodes=(0, 1)
    ).strip()
    flagged = any(
        line[:1] == b"S" or line[:1].islower()
        for line in git(repository, "ls-files", "-v", "-z").split(b"\0")
        if line
    )
    if sparse == b"true" or filters or flagged:
        raise ApplicationError(
            "checkout_unsupported",
            "Delivery requires a full checkout without custom filters, assume-unchanged "
            "or skip-worktree files. Resolve that configuration before retrying.",
            409,
        )
    if git(
        repository, "status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=none"
    ):
        raise ApplicationError(
            "checkout_dirty",
            "The project has local edits or untracked files. Commit, move or remove them "
            "yourself, then retry integration. Flowfield will not stash or discard them.",
            409,
        )


def deliver(
    repository: Path, branch: str, before: str, after: str, expected: CheckoutBinding
) -> None:
    verify(repository, branch, before, expected)
    if any(
        line.startswith(b"160000 ")
        for commit in (before, after)
        for line in git(repository, "ls-tree", "-r", commit).splitlines()
    ):
        raise ApplicationError(
            "checkout_unsupported",
            "Automatic checkout delivery does not support submodules yet.",
            409,
        )
    # Git's checkout protection covers ignored collisions too; read-tree -u alone does not.
    # A service-owned reference-transaction hook enforces our approved base at Git's actual
    # ref transaction, even if an external Git process races between verify and merge.
    # No repository hooks run. The hook can reject the ref update after checkout changes;
    # that partial outcome must remain uncertain, never reset or be declared complete.
    with tempfile.TemporaryDirectory(prefix="flowfield-delivery-hooks-") as hooks:
        hook = Path(hooks) / "reference-transaction"
        probe, observed = Path(hooks) / "probe", Path(hooks) / "observed"
        hook.write_text(
            '#!/bin/sh\n[ "$1" = prepared ] || exit 0\n'
            f"if [ -f {shlex.quote(str(probe))} ]; then "
            f": > {shlex.quote(str(observed))}; exit 0; fi\n"
            'while read -r old new ref; do\ncase "$ref" in\n'
            f"{shlex.quote('refs/heads/' + branch)}|HEAD) "
            f'[ "$old" = {shlex.quote(before)} ] && [ "$new" = {shlex.quote(after)} ] || exit 1;;\n'
            "ORIG_HEAD) ;;\n"
            f'AUTO_MERGE) [ "$new" = {"0" * len(before)} ] || exit 1;;\n'
            "*) exit 1;;\nesac\ndone\n"
        )
        hook.chmod(0o700)
        # Measure support before changing files. This transaction only verifies a ref,
        # then aborts; older Git must not silently skip our conditional-update guard.
        probe.touch()
        try:
            git(
                repository,
                "-c",
                f"core.hooksPath={hooks}",
                "update-ref",
                "--stdin",
                input=f"start\nverify refs/heads/{branch} {before}\nprepare\nabort\n".encode(),
            )
        except ApplicationError as error:
            raise ApplicationError(
                "checkout_blocked",
                "Git could not verify the destination transaction. "
                "Check its branch and locks before retrying.",
                409,
            ) from error
        if not observed.exists():
            raise ApplicationError(
                "checkout_unsupported",
                "This Git installation does not provide the transaction guard required "
                "for checkout delivery. Update Git before retrying.",
                409,
            )
        probe.unlink()
        try:
            git(
                repository,
                "-c",
                f"core.hooksPath={hooks}",
                "-c",
                "submodule.recurse=false",
                "merge",
                "--ff-only",
                "--no-edit",
                "--no-autostash",
                "--no-overwrite-ignore",
                after,
            )
        except ApplicationError as error:
            try:
                verify(repository, branch, before, expected)
            except (ApplicationError, OSError):
                raise ApplicationError(
                    "delivery_uncertain",
                    "Git stopped during delivery. Check the project files and Git state "
                    "before retrying; no files were reset.",
                    409,
                ) from error
            raise ApplicationError(
                "checkout_blocked",
                "Git refused checkout delivery. Check for ignored files that collide "
                "with the candidate or repository locks, then retry integration.",
                409,
            ) from error
    try:
        verify(repository, branch, after, expected)
    except (ApplicationError, OSError) as error:
        raise ApplicationError(
            "delivery_uncertain",
            "Git ran but the complete checkout could not be verified. Check local files "
            "and Git state before retrying.",
            409,
        ) from error
