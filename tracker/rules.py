from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Optional

import yaml

REPLY_PREFIX = re.compile(r"^\s*(re|fw|fwd|automatic reply|auto reply|out of office)\s*:", re.I)


def load_rules(path: str | Path, cfg: dict | None = None) -> dict:
    """rules.yaml, with the triggers replaced by email_rules from config.yaml when set."""
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    data.setdefault("triggers", [])
    data.setdefault("workflow", [])
    data.setdefault("stages", [])
    data.setdefault("buckets", [])
    data.setdefault("milestones", [])
    custom = email_rules_from_config(cfg or {}, data["triggers"])
    if custom is not None:
        data["triggers"] = custom
    return data


def email_rules_from_config(cfg: dict, defaults: list[dict]) -> list[dict] | None:
    rows = cfg.get("email_rules")
    if not isinstance(rows, list) or not rows:
        return None
    base = {str(t.get("id")): t for t in defaults or []}
    out = []
    for row in rows:
        if not isinstance(row, dict) or not str(row.get("id") or "").strip():
            continue
        trigger_id = str(row["id"]).strip()
        merged = dict(base.get(trigger_id) or {})
        merged.pop("subject_regex", None)
        merged.pop("exclude_regex", None)
        merged["id"] = trigger_id
        if row.get("label"):
            merged["label"] = str(row["label"])
        merged["stage"] = str(row.get("stage") or merged.get("stage") or "")
        merged["keywords"] = split_keywords(row.get("keywords"))
        out.append(merged)
    return out


def split_keywords(value) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[,\n]", value)
    return [str(item).strip() for item in value or [] if str(item).strip()]


def keyword_pattern(keyword: str) -> str:
    """Case-insensitive, spacing-tolerant pattern for a plain keyword ("A&T Notification", "WO firm")."""
    parts = re.findall(r"\w+|[^\w\s]", keyword)
    return r"\s*".join(re.escape(part) for part in parts)


def trigger_matches(trigger: dict, subject: str) -> bool:
    pattern = trigger.get("subject_regex")
    if pattern and re.search(pattern, subject, re.I):
        exclude = trigger.get("exclude_regex")
        return not (exclude and re.search(exclude, subject, re.I))
    for keyword in split_keywords(trigger.get("keywords")):
        if re.search(keyword_pattern(keyword), subject, re.I):
            return True
    return False


def is_reply(subject: str) -> bool:
    return bool(REPLY_PREFIX.match(subject or ""))


def classify(subject: str, rules: dict) -> Optional[dict]:
    subject = subject or ""
    reply = is_reply(subject)
    order = bucket_order(rules)
    best = None
    best_rank = -1
    for trigger in rules.get("triggers", []):
        if not trigger_matches(trigger, subject):
            continue
        if reply and trigger.get("no_reply", False):
            continue
        stage = str(trigger.get("stage") or "")
        rank = order.index(stage) if stage in order else -1
        if best is None or rank > best_rank:
            best, best_rank = trigger, rank
    return best


def bucket_order(rules: dict) -> list[str]:
    """Board buckets in order; falls back to the trigger stages."""
    order = [str(b) for b in rules.get("buckets") or []]
    for trigger in rules.get("triggers") or []:
        stage = str(trigger.get("stage") or "")
        if stage and stage not in order:
            order.append(stage)
    return order


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


def milestones(rules: dict) -> list[dict]:
    return list(rules.get("milestones", []) or [])


def milestone_for(rules: dict, row_name: str) -> Optional[dict]:
    for milestone in milestones(rules):
        for pattern in milestone.get("match") or []:
            if re.search(pattern, row_name or "", re.I):
                return milestone
    return None


def parse_level(value: str) -> tuple[str, bool]:
    """'L1R' -> ('L1', True); '2' -> ('L2', False); unknown -> ('', False)."""
    match = re.match(r"^\s*L?\s*(\d)\s*(R)?", str(value or ""), re.I)
    if not match:
        return "", False
    return "L" + match.group(1), bool(match.group(2))


def level_allows(item: dict, level: str) -> bool:
    """Whether a workflow step or milestone applies to a project level; an unknown level gets everything."""
    base, repeat = parse_level(level)
    if not base:
        return True
    levels = [str(value).upper() for value in item.get("levels") or []]
    if levels and base not in levels:
        return False
    return not (repeat and item.get("repeat") is False)


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
