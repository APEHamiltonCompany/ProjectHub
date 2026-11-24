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


def has_project_id(subject: str) -> bool:
    from . import matcher

    return matcher.has_keys(matcher.extract_keys(subject or ""))


def trigger_matches(trigger: dict, subject: str) -> bool:
    pattern = trigger.get("subject_regex")
    if pattern and re.search(pattern, subject, re.I):
        exclude = trigger.get("exclude_regex")
        return not (exclude and re.search(exclude, subject, re.I))
    for keyword in split_keywords(trigger.get("keywords")):
        if re.search(keyword_pattern(keyword), subject, re.I):
            return True
    # Short words like "KO" count only as a whole word, and only when the subject also names an SO, QTE or 020 number.
    for keyword in split_keywords(trigger.get("id_keywords")):
        if re.search(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % keyword_pattern(keyword), subject, re.I) and has_project_id(subject):
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


def checklist_optional_ids(rules: dict) -> list[str]:
    """Ids of the milestones and the non-mandatory steps, in rules order."""
    out = [str(item["id"]) for item in milestones(rules) if item.get("id")]
    out.extend(str(step["id"]) for step in workflow_steps(rules) if step.get("id") and not step.get("mandatory"))
    return out


def checklist_items(rules: dict) -> list[dict]:
    """Rows for Settings > Project checklist, in rules order."""
    steps = [step for step in workflow_steps(rules) if step.get("id")]
    marks = [milestone for milestone in milestones(rules) if milestone.get("id")]

    def step_row(step: dict) -> dict:
        return {
            "id": str(step["id"]),
            "title": step.get("title", step["id"]),
            "stage": step.get("stage", ""),
            "kind": "step",
            "mandatory": bool(step.get("mandatory")),
            "milestone": str(step.get("milestone") or ""),
        }

    stages = workflow_stages(rules)
    for milestone in marks:
        if milestone.get("stage", "") not in stages:
            stages.append(milestone.get("stage", ""))
    rows = []
    for stage in stages:
        rows.extend(step_row(step) for step in steps if step.get("stage", "") == stage and not step.get("milestone"))
        for milestone in marks:
            if milestone.get("stage", "") != stage:
                continue
            rows.append(
                {
                    "id": str(milestone["id"]),
                    "title": milestone.get("title", milestone["id"]),
                    "stage": stage,
                    "kind": "milestone",
                    "mandatory": False,
                    "milestone": "",
                }
            )
            rows.extend(step_row(step) for step in steps if step.get("milestone") == milestone["id"])
    listed = {row["id"] for row in rows}
    rows.extend(step_row(step) for step in steps if str(step["id"]) not in listed)
    return rows


def enabled_optional(cfg: dict | None) -> set[str]:
    values = (cfg or {}).get("checklist_optional")
    if not isinstance(values, list):
        return set()
    return {str(value) for value in values if isinstance(value, str)}


def apply_checklist(rules: dict, cfg: dict | None) -> dict:
    """Rules with only the mandatory and the user's optional steps; each milestone carries an enabled flag."""
    enabled = enabled_optional(cfg)
    out = dict(rules)
    workflow = []
    for step in workflow_steps(rules):
        if step.get("mandatory"):
            item = dict(step)
            item.pop("levels", None)
            item.pop("repeat", None)
            workflow.append(item)
        elif str(step.get("id") or "") in enabled:
            workflow.append(dict(step))
    out["workflow"] = workflow
    out["milestones"] = [
        dict(milestone, enabled=str(milestone.get("id") or "") in enabled) for milestone in milestones(rules)
    ]
    return out


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
