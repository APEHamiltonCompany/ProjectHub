"""One-time import of projects from older assignment emails (Projects page > Import projects from email)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from . import extract, matcher, notes
from . import projects as projects_mod
from . import rules as rules_mod
from .models import Email, Project
from .sources import ape_sheet

IMPORT_DAYS = 365
COMPLETION = "Completion"


def _numbers(row: dict) -> set[str]:
    return set(row["so"]) | set(row["qte"]) | ({row["project_no"]} if row["project_no"] else set())


def _key(parsed: dict) -> str:
    so, qte = list(parsed.get("so") or []), list(parsed.get("qte") or [])
    return parsed.get("project_no") or (so[0] if so else "") or (qte[0] if qte else "")


def build_preview(
    cfg: dict, rules: dict, projects: list[Project], emails: list[Email], sheet_rows: list[dict] | None = None
) -> list[dict]:
    """Assignment emails addressed to me (To: line) whose numbers match no project folder, one row per project,
    with the trigger dates and stage its later emails reached. Completed projects and rows whose folder already
    exists start unticked."""
    from .tracker import furthest_stage

    index = matcher.build_index(projects)
    me = cfg.get("me") or {}
    layout = matcher.folder_layout(cfg)
    vault = Path(cfg.get("vault") or "")
    rows: list[dict] = []
    by_number: dict[str, dict] = {}
    ordered = sorted(emails, key=lambda e: e.date)
    for email in ordered:
        if rules_mod.is_reply(email.subject):
            continue
        trigger = rules_mod.classify(email.subject, rules)
        if not trigger or trigger.get("id") != "assignment":
            continue
        if matcher.addressed_to_me(email.to, me) is False:
            continue
        if matcher.match_email(email, index)[0] is not None:
            continue
        parsed = matcher.parse_assignment_subject(email.subject)
        if not _key(parsed):
            continue
        numbers = set(parsed["so"]) | set(parsed["qte"]) | ({parsed["project_no"]} if parsed.get("project_no") else set())
        row = next((by_number[n] for n in sorted(numbers) if n in by_number), None)
        if row is None:
            row = {
                "id": email.message_id,
                "entry_id": email.entry_id,
                "subject": email.subject,
                "received": email.date.astimezone().isoformat(timespec="seconds"),
                "customer": parsed.get("customer", ""),
                "so": [],
                "qte": [],
                "project_no": parsed.get("project_no", ""),
                "level": parsed.get("level", ""),
                "description": parsed.get("description", ""),
                "triggers": {},
            }
            rows.append(row)
        for name in ("so", "qte"):
            row[name].extend(value for value in parsed.get(name) or [] if value not in row[name])
        row["project_no"] = row["project_no"] or parsed.get("project_no", "")
        for number in _numbers(row):
            by_number.setdefault(number, row)

    for email in ordered:
        if rules_mod.is_reply(email.subject):
            continue
        trigger = rules_mod.classify(email.subject, rules)
        if not trigger:
            continue
        keys = matcher.extract_keys(email.subject)
        found = next((by_number[n] for n in sorted(keys["project_no"] | keys["so"] | keys["qte"]) if n in by_number), None)
        if found is None:
            continue
        day = email.date.astimezone().date().isoformat()
        current = found["triggers"].get(trigger["id"])
        if trigger["id"] == "assignment":
            found["triggers"][trigger["id"]] = min(current or day, day)
        else:
            found["triggers"][trigger["id"]] = max(current or day, day)

    sheet = {}
    if sheet_rows:
        temp = [Project(folder="", name=row["id"], project_no=row["project_no"], so=row["so"], qte=row["qte"]) for row in rows]
        matched = ape_sheet.match(sheet_rows, temp, str((cfg.get("ape_sheet") or {}).get("my_initials") or ""))
        sheet = {p.name: matched[p.key] for p in temp if p.key in matched}

    for row in rows:
        triggers = {key: date.fromisoformat(value) for key, value in row["triggers"].items()}
        row["key"] = _key(row)
        row["stage"] = furthest_stage(triggers, rules)
        hit = sheet.get(row["id"]) or {}
        row["sheet_status"] = str(hit.get("status") or "")
        sheet_done = hit.get("tab") == ape_sheet.COMPLETED_TAB or ape_sheet.status_stage(row["sheet_status"]) == COMPLETION
        row["completed"] = row["stage"] == COMPLETION or sheet_done
        row["folder"] = matcher.folder_name_for(row["customer"], row["so"], row["qte"], row["project_no"], layout)
        parts = matcher.split_folder(row["folder"])
        row["folder_exists"] = bool(parts) and notes.lp(vault.joinpath(*parts)).exists()
        notes_list = []
        if row["completed"]:
            notes_list.append("completed" + (" (tracker sheet)" if sheet_done and row["stage"] != COMPLETION else ""))
        if row["folder_exists"]:
            notes_list.append("a folder with this name already exists")
        row["notes"] = ", ".join(notes_list)
        row["checked"] = not row["completed"] and not row["folder_exists"]
    rows.sort(key=lambda r: r["received"], reverse=True)
    return rows


def pending_from_row(row: dict, body_text: str = "") -> dict:
    body = extract.parse_assignment_body(body_text) if body_text else {}
    return {
        "message_id": row["id"],
        "subject": row["subject"],
        "received": row["received"],
        "parsed": {
            "customer": row["customer"],
            "so": list(row["so"]),
            "qte": list(row["qte"]),
            "project_no": row["project_no"],
            "level": row["level"],
            "description": row["description"],
        },
        "sharepoint": body.get("sharepoint", ""),
        "team": list(body.get("team") or []),
        "checklist": list(body.get("checklist") or []),
        "suggested_folder": row["folder"],
        "status": "new",
    }


def create_rows(cfg: dict, rows: list[dict], body_for=lambda row: "") -> tuple[list[dict], list[str]]:
    """Create the picked rows. Returns ([{key, folder, triggers}], failures). Files that need a choice (test
    matrix, project document) get a task to pick them on the project page."""
    created: list[dict] = []
    failed: list[str] = []
    for row in rows:
        entry = pending_from_row(row, body_for(row))
        try:
            info = projects_mod.create_from_pending(cfg, entry)
        except (OSError, ValueError) as exc:
            failed.append("%s: %s" % (row["folder"], exc))
            continue
        key = matcher.project_from_folder(info["folder"]).key if Path(info["folder"]).is_dir() else row["key"]
        created.append({"key": key, "folder": info["folder"], "triggers": dict(row["triggers"]), "id": row["id"]})
    return created, failed
