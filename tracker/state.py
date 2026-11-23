from __future__ import annotations

import json
from pathlib import Path

EMPTY = {
    "processed": [],
    "projects": {},
    "pending": {},
    "email_overrides": {},
    "due_overrides": {},
    "computed_due": {},
    "schedule_ticked": {},
    "schedule_links": {},
    "last_run": "",
    "tick_log": [],
    "user_links": {},
    "ui": {},
}


def load(path: str | Path) -> dict:
    target = Path(path)
    if not target.is_file():
        return dict(EMPTY, processed=[], projects={})
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(EMPTY, processed=[], projects={})
    if not isinstance(data, dict):
        return dict(EMPTY, processed=[], projects={})
    data.setdefault("processed", [])
    data.setdefault("projects", {})
    data.setdefault("pending", {})
    data.setdefault("email_overrides", {})
    data.setdefault("due_overrides", {})
    data.setdefault("computed_due", {})
    data.setdefault("schedule_ticked", {})
    data.setdefault("schedule_links", {})
    data.setdefault("last_run", "")
    data.setdefault("tick_log", [])
    data.setdefault("user_links", {})
    data.setdefault("ui", {})
    return data


def save(path: str | Path, data: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
