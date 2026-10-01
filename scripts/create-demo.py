"""Create the disposable Harbor repository and seed its board through the local API.

Developer fixture only; refuses existing paths/projects and never calls a model.
"""

import argparse
import json
import shutil
import subprocess
from pathlib import Path

import httpx

TASKS = [
    (
        "catalog",
        "Load and validate the catalog",
        "catalog",
        [],
        "feature",
        "Implement load_catalog(path) in catalog.py. Read UTF-8 JSON "
        "records with unique id, title, "
        "author, and tags. Reject malformed records with useful errors. "
        "Test valid, empty, duplicate "
        "and malformed input using unittest; no network or dependencies.",
    ),
    (
        "filter",
        "Filter books by author and tag",
        "catalog",
        ["catalog"],
        "feature",
        "Implement pure filtering in filters.py and a list CLI command. "
        "Author matches case-insensitively; "
        "tag matches exactly. Combining filters uses AND. Test empty results and combinations.",
    ),
    (
        "stats",
        "Summarize catalog counts",
        "catalog",
        ["catalog"],
        "feature",
        "Implement stats.py counts by author and tag, including empty catalogs. Keep this pure so "
        "CSV and HTML can reuse it. Cover behavior with unittest.",
    ),
    (
        "csv",
        "Serialize filtered books as CSV",
        "csv",
        ["catalog"],
        "feature",
        "Implement csv_export.py using the standard csv module. Columns: id,title,author,tags. "
        "Join tags with semicolons. Follow the project output convention once answered. "
        "Test commas, quotes, Unicode, and empty data.",
    ),
    (
        "csv-cli",
        "Add the CSV export command",
        "csv",
        ["filter", "csv"],
        "feature",
        "Add export-csv with author/tag filters and an explicit output path. Refuse overwriting "
        "unless --force is given. Test exported content and preservation of existing files.",
    ),
    (
        "html",
        "Create a shareable HTML reading report",
        "html",
        ["filter", "stats"],
        "feature",
        "Add report-html with filters, counts, and book list. Escape user text, use no scripts or "
        "external assets, and refuse overwrites without --force. Follow "
        "the chosen output convention. "
        "Test Unicode, HTML injection, and empty results.",
    ),
    (
        "errors",
        "Make command failures readable",
        "html",
        ["csv-cli", "html"],
        "maintenance",
        "Ensure bad paths, invalid JSON and output failures give concise stderr and nonzero exit. "
        "Successful commands return zero. Add CLI integration tests and document example commands.",
    ),
    (
        "sample",
        "Add a representative sample catalog",
        "catalog",
        [],
        "maintenance",
        "Expand sample.json to at least eight fictional books with shared authors/tags, Unicode "
        "and punctuation. Add a short usage example to README; no code changes required.",
    ),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="New directory for the disposable repository")
    parser.add_argument("--port", type=int, default=8771)
    args = parser.parse_args()
    root = args.path.expanduser().absolute()
    if root.exists() or root.is_symlink():
        parser.error("Choose a new directory; existing files are never reset.")
    if not 1 <= args.port <= 65535:
        parser.error("Port must be 1–65535.")
    with httpx.Client(base_url=f"http://127.0.0.1:{args.port}/api", trust_env=False) as client:

        def post(path: str, data: dict) -> dict:
            response = client.post(path, json=data)
            response.raise_for_status()
            return response.json()

        existing = client.get("/projects/harbor")
        if existing.status_code != 404:
            existing.raise_for_status()
            parser.error("Harbor already exists; use fresh service state for another fixture.")
        root.mkdir(parents=True)
        post(
            "/projects/initialize",
            {"path": str(root), "id": "harbor", "name": "Harbor", "task_prefix": "HAR"},
        )
        shutil.copytree(
            Path(__file__).resolve().parents[1] / "examples" / "harbor",
            root,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        for command in (
            ["init", "-b", "main"],
            ["add", "."],
            [
                "-c",
                "user.name=Harbor Demo",
                "-c",
                "user.email=harbor@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-m",
                "Start disposable Harbor fixture",
            ],
        ):
            subprocess.run(["git", "-C", str(root), *command], check=True, capture_output=True)
        project = client.get("/projects/harbor").json()
        response = client.put(
            "/projects/harbor",
            json={
                "expected_revision": project["revision"],
                "description": "Build a small offline reading catalog: browse/filter books, "
                "export CSV, "
                "and share a static HTML report. Python standard library only. No "
                "accounts or network.",
            },
        )
        response.raise_for_status()
        for identity, title in (
            ("catalog", "Catalog and search"),
            ("csv", "CSV export"),
            ("html", "Shareable report"),
        ):
            post("/projects/harbor/milestones", {"id": identity, "title": title})
        for identity, title, milestone, dependencies, kind, body in TASKS:
            post(
                "/projects/harbor/tasks",
                {
                    "id": identity,
                    "title": title,
                    "milestone_id": milestone,
                    "dependencies": dependencies,
                    "task_type": kind,
                    "body": body,
                    "status": "up_next" if identity in ("catalog", "sample", "csv") else "backlog",
                },
            )
        post(
            "/projects/harbor/questions",
            {
                "id": "output-convention",
                "question": "Which text encoding should Harbor exports use?",
                "context": "CSV and HTML need one shared output convention. Catalog and "
                "sample work can continue.",
                "recommendation": "UTF-8 without a byte-order mark, to keep both exports portable.",
                "choices": ["UTF-8 without BOM", "UTF-8; add BOM only to CSV"],
                "affected_task_ids": ["csv", "html"],
                "blocking_scope": "Choosing the export encoding",
                "author": "demo",
            },
        )
    print(
        json.dumps(
            {
                "path": str(root),
                "board": f"http://127.0.0.1:{args.port}/projects/harbor",
                "milestones": 3,
                "tasks": len(TASKS),
                "models_started": 0,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
