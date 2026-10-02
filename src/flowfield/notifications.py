"""Durable notices and channel receipts; producers retain authority over work state."""

import hashlib
import json
import sqlite3
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from flowfield.application import Workspace, now
from flowfield.attention import attention_notices

RETAINED_NOTICES = 200


class NoticeAction(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    href: str = Field(min_length=1, max_length=2000)

    @field_validator("href")
    @classmethod
    def safe_href(cls, value: str) -> str:
        if any(c in value for c in ("\\", "\r", "\n")) or not (
            (value.startswith("/") and not value.startswith("//")) or value.startswith("https://")
        ):
            raise ValueError("Use a local path or HTTPS link")
        return value


class NoticeCommand(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    command: str = Field(min_length=1, max_length=500)


class NoticeContent(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
    title: str = Field(min_length=1, max_length=600)
    message: str = Field(min_length=1, max_length=4000)
    scope: str | None = Field(default=None, max_length=200)
    actions: list[NoticeAction] = Field(default_factory=list, max_length=3)
    commands: list[NoticeCommand] = Field(default_factory=list, max_length=3)


class Notification(NoticeContent):
    id: int
    key: str
    source: str
    created_at: str


class NotificationPage(BaseModel):
    items: list[Notification]
    through: int


class OperationNotice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=500)
    occurrence: str | None = Field(default=None, max_length=64)
    project_id: str | None = Field(default=None, max_length=64)
    title: str = Field(min_length=1, max_length=600)
    message: str = Field(min_length=1, max_length=4000)
    href: str | None = Field(default=None, max_length=2000)
    action: str | None = Field(default=None, max_length=100)

    @field_validator("href")
    @classmethod
    def safe_href(cls, value: str | None) -> str | None:
        return NoticeAction.safe_href(value) if value is not None else None


class NotificationSettings(BaseModel):
    browser_enabled: bool = False


def state(db: sqlite3.Connection, name: str) -> dict[str, Any]:
    row = db.execute("SELECT data FROM notification_state WHERE name=?", (name,)).fetchone()
    return json.loads(row[0]) if row else {}


def save_state(db: sqlite3.Connection, name: str, value: dict[str, Any]) -> None:
    data = json.dumps(value, sort_keys=True)
    db.execute(
        "INSERT INTO notification_state VALUES (?,?) ON CONFLICT(name) DO UPDATE SET "
        "data=excluded.data WHERE data != excluded.data",
        (name, data),
    )


def publish(db: sqlite3.Connection, source: str, key: str, content: NoticeContent) -> None:
    db.execute(
        "INSERT INTO notifications(key,source,data,created_at) VALUES (?,?,?,?) "
        "ON CONFLICT(key) DO NOTHING",
        (key, source, content.model_dump_json(), now()),
    )
    db.execute(
        "DELETE FROM notifications WHERE id NOT IN "
        "(SELECT id FROM notifications ORDER BY id DESC LIMIT ?)",
        (RETAINED_NOTICES,),
    )


def notification(row: sqlite3.Row) -> Notification:
    return Notification(
        **json.loads(row["data"]),
        id=row["id"],
        key=row["key"],
        source=row["source"],
        created_at=row["created_at"],
    )


class Notifications:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def page(self) -> NotificationPage:
        with self.workspace.connection() as db:
            rows = db.execute(
                "SELECT * FROM notifications WHERE dismissed_at IS NULL AND resolved_at IS NULL "
                "ORDER BY id DESC LIMIT ?",
                (RETAINED_NOTICES,),
            ).fetchall()
            through = db.execute("SELECT coalesce(max(id),0) FROM notifications").fetchone()[0]
            return NotificationPage(items=[notification(row) for row in rows], through=through)

    def operation(self, notice: OperationNotice) -> NotificationPage:
        content = NoticeContent(
            title=notice.title,
            message=notice.message,
            actions=[NoticeAction(label=notice.action or "Open", href=notice.href)]
            if notice.href
            else [],
        )
        # Same condition stays dismissed across reloads; changed content is a new occurrence.
        key = "operation:" + hashlib.sha256(notice.model_dump_json().encode()).hexdigest()
        with self.workspace.connection(write=True, notify=False) as db:
            if notice.project_id:
                project = db.execute(
                    "SELECT name FROM projects WHERE id=?", (notice.project_id,)
                ).fetchone()
                content.scope = project[0] if project else notice.project_id
            publish(db, "operation", key, content)
        return self.page()

    def dismiss(self, *, ids: list[int], through: int | None = None) -> NotificationPage:
        with self.workspace.connection(write=True, notify=False) as db:
            if through is not None:
                db.execute(
                    "UPDATE notifications SET dismissed_at=? WHERE id<=? AND dismissed_at IS NULL",
                    (now(), through),
                )
            elif ids:
                db.executemany(
                    "UPDATE notifications SET dismissed_at=? WHERE id=? AND dismissed_at IS NULL",
                    [(now(), value) for value in ids],
                )
        return self.page()

    def settings(self) -> NotificationSettings:
        with self.workspace.connection() as db:
            return NotificationSettings.model_validate(state(db, "browser"))

    def configure(self, value: NotificationSettings) -> NotificationSettings:
        with self.workspace.connection(write=True, notify=False) as db:
            previous = NotificationSettings.model_validate(state(db, "browser"))
            if value.browser_enabled and not previous.browser_enabled:
                # Opt-in starts with the current feed as its baseline, not a backlog of popups.
                db.execute(
                    "INSERT OR IGNORE INTO notification_deliveries "
                    "SELECT id,'browser',? FROM notifications",
                    (now(),),
                )
            save_state(db, "browser", value.model_dump())
        return value

    def claim_browser(self, *, deliver: bool) -> list[Notification]:
        with self.workspace.connection(write=True, notify=False) as db:
            if not state(db, "browser").get("browser_enabled"):
                return []
            rows = db.execute(
                "SELECT n.* FROM notifications n WHERE dismissed_at IS NULL "
                "AND resolved_at IS NULL AND NOT EXISTS (SELECT 1 FROM notification_deliveries d "
                "WHERE d.notification_id=n.id AND d.channel='browser') ORDER BY id LIMIT 20"
            ).fetchall()
            db.executemany(
                "INSERT INTO notification_deliveries VALUES (?,'browser',?)",
                [(row["id"], now()) for row in rows],
            )
            # One delivery attempt across tabs/browsers. The durable feed covers lost popups.
            return [notification(row) for row in rows] if deliver else []

    def sync_attention(self) -> None:
        items = attention_notices(self.workspace)
        keys = {"attention:" + item.key for item in items}
        with self.workspace.connection(write=True, notify=False) as db:
            previous = set(state(db, "attention").get("keys", []))
            for item in items:
                key = "attention:" + item.key
                if key not in previous:
                    publish(
                        db,
                        "attention",
                        key,
                        NoticeContent(
                            title=item.title,
                            message=item.label,
                            actions=[NoticeAction(label="Open task", href=item.href)],
                        ),
                    )
            rows = db.execute(
                "SELECT id,key FROM notifications WHERE source='attention' AND resolved_at IS NULL"
            ).fetchall()
            db.executemany(
                "UPDATE notifications SET resolved_at=? WHERE id=?",
                [(now(), row["id"]) for row in rows if row["key"] not in keys],
            )
            # Remember active producer identities even when retention prunes their old records.
            save_state(db, "attention", {"keys": sorted(keys)})
