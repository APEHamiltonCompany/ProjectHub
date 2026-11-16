from __future__ import annotations

from pathlib import Path
from typing import Iterable

from ..models import Email
from . import eml_folder, outlook_com

ROOT = Path(__file__).resolve().parents[2]


def resolve_folder(folder: str | Path) -> Path:
    path = Path(folder)
    return path if path.is_absolute() else ROOT / path


def dedupe(emails: Iterable[Email]) -> list[Email]:
    seen: dict[str, Email] = {}
    for item in emails:
        if item.message_id in seen:
            continue
        seen[item.message_id] = item
    return list(seen.values())


def collect_all(config: dict, since_days: int = 120) -> list[Email]:
    folders = [resolve_folder(folder) for folder in (config.get("eml_folders") or [])]
    if config.get("scan_project_folders") and config.get("vault"):
        folders.append(config["vault"])
    emails: list[Email] = []
    emails.extend(eml_folder.collect(folders, since_days=since_days, recursive=True))
    for reader in (outlook_com.collect, outlook_com.collect_calendar):
        try:
            emails.extend(reader(config, since_days=since_days))
        except Exception as exc:
            print("WARNING: Outlook %s failed (%s); is classic Outlook set up?" % (reader.__name__, exc))
    return dedupe(emails)
