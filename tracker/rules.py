from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Optional

import yaml

REPLY_PREFIX = re.compile(r"^\s*(re|fw|fwd)\s*:", re.I)


def load_rules(path: str | Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    data.setdefault("triggers", [])
    data.setdefault("workflow", [])
    data.setdefault("stages", [])
    return data


def is_reply(subject: str) -> bool:
    return bool(REPLY_PREFIX.match(subject or ""))


def classify(subject: str, rules: dict) -> Optional[dict]:
    subject = subject or ""
    reply = is_reply(subject)
    for trigger in rules.get("triggers", []):
        pattern = trigger.get("subject_regex")
        if not pattern:
            continue
        if not re.search(pattern, subject, re.I):
            continue
        exclude = trigger.get("exclude_regex")
        if exclude and re.search(exclude, subject, re.I):
            continue
        if reply and trigger.get("no_reply", False):
            continue
        return trigger
    return None


def trigger_by_id(rules: dict, trigger_id: str) -> Optional[dict]:
    for trigger in rules.get("triggers", []):
        if trigger.get("id") == trigger_id:
            return trigger
    return None


def deliverable_titles(rules: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in rules.get("workflow", []) or []:
        out[item["id"]] = item.get("title", item["id"])
    return out


def workflow_steps(rules: dict) -> list[dict]:
    return list(rules.get("workflow", []) or [])


def workflow_stages(rules: dict) -> list[str]:
    stages = [str(stage) for stage in rules.get("stages", []) or []]
    for item in workflow_steps(rules):
        stage = item.get("stage")
        if stage and stage not in stages:
            stages.append(stage)
    return stages


def add_business_days(start: date, n: int, holidays: Iterable[date] = ()) -> date:
    holiday_set = set(holidays)
    current = start
    added = 0
    while added < n:
        current += timedelta(days=1)
        if current.weekday() < 5 and current not in holiday_set:
            added += 1
    return current


def parse_holidays(values: Iterable) -> list[date]:
    out = []
    for value in values or []:
        if isinstance(value, date):
            out.append(value)
        else:
            out.append(date.fromisoformat(str(value)))
    return out
