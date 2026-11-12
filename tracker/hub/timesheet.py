from __future__ import annotations

from datetime import date, timedelta

NONE_ROW = "none"
NONE_LABEL = "No project (admin/overhead)"


def week_bounds(day: date) -> tuple[date, date]:
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


def _day(value) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _round(value: float, step: float) -> float:
    if step and step > 0:
        return round(round(value / step) * step, 2)
    return round(value, 2)


def _hours_text(value: float) -> str:
    return "%g" % round(value, 2)


def _label(customer: str, ident: str, fallback: str) -> str:
    parts = [part for part in (customer, ident) if part]
    if parts:
        return " ".join(parts)
    return fallback


def _ident(project: dict) -> str:
    return " / ".join(list(project.get("so") or []) + list(project.get("qte") or []))


def _project_id(project: dict, index: dict) -> str:
    sheet = project.get("sheet") or {}
    found = str(sheet.get("project_id") or "")
    if found:
        return found
    candidates = [project.get("project_no")] + list(project.get("so") or []) + list(project.get("qte") or [])
    for value in candidates:
        entry = index.get(str(value or "")) or {}
        if entry.get("project_id"):
            return str(entry["project_id"])
    return ""


def build(
    items: list[dict],
    snapshot: dict,
    tick_log: list[dict],
    edits: dict,
    start: date,
    end: date,
    round_to: float = 0.25,
) -> dict:
    first = _day(start) or date.today()
    last = _day(end) or first
    if last < first:
        first, last = last, first
    days = [first + timedelta(days=offset) for offset in range((last - first).days + 1)]
    day_set = {day.isoformat() for day in days}
    index = snapshot.get("ape_sheet_index") or {}
    projects = snapshot.get("projects") or []
    by_key = {str(project.get("key") or ""): project for project in projects}
    rows: dict[str, dict] = {}

    def row_for(row_id: str, project_id: str, label: str, key: str, ref: str, missing_id: bool) -> dict:
        row = rows.get(row_id)
        if row is None:
            row = {
                "row_id": row_id,
                "project_id": project_id,
                "label": label,
                "key": key,
                "ref": ref,
                "missing_id": missing_id,
                "cells": {},
                "activity": {},
                "tasks": [],
                "total": 0.0,
            }
            rows[row_id] = row
        return row

    def cell(row: dict, day_iso: str) -> dict:
        cells = row["cells"]
        if day_iso not in cells:
            cells[day_iso] = {"hours": 0.0, "auto": 0.0, "edited": False, "sources": []}
        return cells[day_iso]

    def project_row(project: dict) -> dict:
        key = str(project.get("key") or "")
        project_id = _project_id(project, index)
        label = _label(
            str(project.get("customer") or ""), _ident(project), str(project.get("name") or key)
        )
        return row_for(project_id or "key:" + key, project_id, label, key, "", not project_id)

    for item in items or []:
        if item.get("mark") != "x":
            continue
        hours = item.get("hours")
        if not hours or hours <= 0:
            continue
        all_days = [str(value) for value in item.get("worked_days") or []]
        in_range = [value for value in all_days if value in day_set]
        if not all_days or not in_range:
            continue
        key = str(item.get("key") or "")
        ref = str(item.get("ref") or "")
        if key:
            project = by_key.get(key)
            row = project_row(project) if project else row_for("key:" + key, "", key, key, "", True)
        elif ref:
            entry = index.get(ref) or {}
            project_id = str(entry.get("project_id") or "")
            label = _label(str(entry.get("customer") or ""), str(entry.get("so") or ""), project_id or ref)
            row = row_for(project_id or "ref:" + ref, project_id, label, "", ref, not project_id)
        else:
            row = row_for(NONE_ROW, "", NONE_LABEL, "", "", False)
        share = float(hours) / len(all_days)
        source = "task: %s (%sh)" % (item.get("title") or "", _hours_text(hours))
        for day_iso in in_range:
            entry = cell(row, day_iso)
            entry["auto"] += share
            entry["sources"].append(source)
        row["tasks"].append(
            {"title": item.get("title") or "", "hours": float(hours), "days": in_range}
        )

    for project in projects:
        row = None
        for email in project.get("emails") or []:
            if not str(email.get("message_id") or "").startswith("calendar:"):
                continue
            minutes = int(email.get("minutes") or 0)
            if minutes <= 0:
                continue
            day = _day(email.get("start") or email.get("date"))
            if day is None or day.isoformat() not in day_set:
                continue
            if row is None:
                row = project_row(project)
            hours = minutes / 60.0
            entry = cell(row, day.isoformat())
            entry["auto"] += hours
            entry["sources"].append(
                "meeting: %s (%sh)" % (email.get("subject") or "", _hours_text(hours))
            )

    for entry in tick_log or []:
        key = str(entry.get("key") or "")
        if not key:
            continue
        day = _day(entry.get("at"))
        if day is None or day.isoformat() not in day_set:
            continue
        project = by_key.get(key)
        row = project_row(project) if project else row_for("key:" + key, "", key, key, "", True)
        row["activity"].setdefault(day.isoformat(), []).append(
            "ticked: %s" % (entry.get("title") or entry.get("task_id") or "task")
        )

    for project in projects:
        row = None
        for email in project.get("emails") or []:
            if str(email.get("message_id") or "").startswith("calendar:"):
                continue
            day = _day(email.get("date"))
            if day is None or day.isoformat() not in day_set:
                continue
            if row is None:
                row = project_row(project)
            row["activity"].setdefault(day.isoformat(), []).append(
                "email: %s" % (email.get("subject") or "")
            )

    for composite, value in (edits or {}).items():
        row_id, _sep, day_text = str(composite).partition("|")
        if not row_id or day_text not in day_set:
            continue
        try:
            hours = float(value)
        except (TypeError, ValueError):
            continue
        row = rows.get(row_id)
        if row is None:
            if row_id == NONE_ROW:
                row = row_for(NONE_ROW, "", NONE_LABEL, "", "", False)
            elif row_id.startswith("key:"):
                row = row_for(row_id, "", row_id, row_id[4:], "", True)
            elif row_id.startswith("ref:"):
                row = row_for(row_id, "", row_id, "", row_id[4:], True)
            else:
                row = row_for(row_id, row_id, row_id, "", "", False)
        entry = cell(row, day_text)
        entry["hours"] = hours
        entry["edited"] = True

    result_rows = []
    for row in rows.values():
        total = 0.0
        for entry in row["cells"].values():
            auto = _round(entry["auto"], round_to)
            entry["auto"] = auto
            entry["hours"] = round(entry["hours"], 2) if entry["edited"] else auto
            total += entry["hours"]
        row["total"] = round(total, 2)
        result_rows.append(row)

    def sort_key(row: dict) -> tuple:
        if row["project_id"]:
            return (0, row["project_id"], row["row_id"])
        if row["row_id"] != NONE_ROW:
            return (1, row["row_id"], "")
        return (2, "", "")

    result_rows.sort(key=sort_key)
    totals = {}
    for day in days:
        day_iso = day.isoformat()
        totals[day_iso] = round(
            sum(row["cells"].get(day_iso, {}).get("hours", 0.0) for row in result_rows), 2
        )
    return {
        "days": [day.isoformat() for day in days],
        "rows": result_rows,
        "totals": totals,
        "grand": round(sum(totals.values()), 2),
    }


def spread(total: float, days, round_to: float = 0.25) -> dict:
    values = [str(day) for day in days or []]
    if not values:
        return {}
    step = round_to if round_to and round_to > 0 else 0.25
    units = int(round(float(total or 0) / step))
    if units <= 0:
        return {day: 0.0 for day in values}
    base = units // len(values)
    extra = units - base * len(values)
    result = {}
    for position, day in enumerate(values):
        count = base + (1 if position < extra else 0)
        result[day] = round(count * step, 2)
    return result
