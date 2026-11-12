from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

import yaml

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tracker import dashboard, extract, locking, matcher, notes
from tracker import projects as projects_mod
from tracker import rules as rules_mod
from tracker import settings as settings_mod
from tracker import state as state_mod
from tracker.models import Email, Project, Task
from tracker.sources import collect_all
from tracker.sources import ape_sheet

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
LOG_LIMIT = 1024 * 1024


def out(text: str = "") -> None:
    sys.stdout.write(text.encode("ascii", "replace").decode("ascii") + "\n")


def load_config(path: str | Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    return settings_mod.apply_overlay(cfg, settings_mod.load_overlay(settings_mod.settings_path_for(path)))


def render_template(text: str, values: dict) -> str:
    result = text
    for key, value in values.items():
        result = result.replace("{" + key + "}", str(value))
    return result


def yaml_list(values: list[str]) -> str:
    return ", ".join('"%s"' % v for v in values)


def task_prefix(project: Project) -> str:
    if project.project_no:
        return "p" + project.project_no
    slug = re.sub(r"[^a-z0-9]", "", project.key.lower())
    return "p" + (slug or "project")


def create_project(cfg: dict, email: Email, parsed: dict, stage: str, dry_run: bool) -> Project | None:
    body_parsed = extract.parse_assignment_body(email.body_text)
    entry = projects_mod.pending_entry(email, parsed, body_parsed)
    try:
        info = projects_mod.create_from_pending(cfg, entry, dry_run=dry_run)
    except ValueError as exc:
        out("  cannot create project: %s" % exc)
        return None
    if dry_run:
        out("  would create project folder: %s" % info["folder"])
        out("  would create note: %s" % info["note_path"])
    else:
        out("  created project folder: %s" % info["folder"])
    return Project(
        folder=info["folder"],
        name=Path(info["folder"]).name,
        note_path=info["note_path"],
        customer=parsed["customer"],
        project_no=parsed["project_no"],
        so=list(parsed["so"]),
        qte=list(parsed["qte"]),
        level=parsed["level"],
        stage=stage,
    )


def ensure_dashboard_note(cfg: dict, dry_run: bool) -> None:
    if not cfg.get("install_dashboard_note", True):
        return
    vault = Path(cfg.get("vault") or "")
    template_path = Path(cfg.get("templates_dir") or (ROOT / "templates")) / "Dashboard.md"
    target = vault / "Dashboard.md"
    if not vault.is_dir() or not template_path.is_file() or target.exists():
        return
    if dry_run:
        out("would copy Dashboard.md into the vault root: %s" % target)
        return
    target.write_text(template_path.read_text(encoding="utf-8"), encoding="utf-8")
    out("copied Dashboard.md into the vault root: %s" % target)


def open_info(email: Email) -> dict:
    if email.entry_id:
        return {"kind": "outlook", "entry_id": email.entry_id}
    path = str(email.source_path or "")
    if path.lower().endswith(".eml"):
        return {"kind": "eml", "path": path}
    return {}


def unmatched_entry(when: date, email: Email, reason: str, keys: dict) -> dict:
    entry = {
        "date": when.isoformat(),
        "subject": email.subject,
        "reason": reason,
        "message_id": email.message_id,
        "keys": {name: sorted(values) for name, values in keys.items()},
        "open": open_info(email),
    }
    if email.message_id.startswith("calendar:"):
        entry["minutes"] = calendar_minutes(email)
    return entry


def calendar_minutes(email: Email) -> int:
    end = getattr(email, "end", None)
    if not end or not email.date:
        return 0
    try:
        seconds = (end - email.date).total_seconds()
    except TypeError:
        return 0
    return max(0, int(round(seconds / 60.0)))


def prune_pending(pending: dict, projects: list[Project]) -> dict:
    numbers = {p.project_no for p in projects if p.project_no}
    so = {value for p in projects for value in p.so}
    qte = {value for p in projects for value in p.qte}
    kept = {}
    for message_id, entry in pending.items():
        parsed = entry.get("parsed") or {}
        if (
            parsed.get("project_no") in numbers
            or set(parsed.get("so") or []) & so
            or set(parsed.get("qte") or []) & qte
        ):
            continue
        kept[message_id] = entry
    return kept


def collect_trigger_dates(
    cfg: dict,
    rules: dict,
    projects: list[Project],
    index: dict,
    emails: list[Email],
    state: dict,
    dry_run: bool,
) -> tuple[dict, list[dict], dict, dict]:
    triggers: dict[str, dict[str, date]] = {}
    unmatched: list[dict] = []
    matched: dict[str, list[tuple[Email, str, bool]]] = {}
    pending = dict(state.get("pending") or {})
    overrides = state.get("email_overrides") or {}
    stored = state.get("projects") or {}

    for email in sorted(emails, key=lambda e: e.date):
        keys = matcher.extract_keys(email.subject)
        body_keys = matcher.extract_keys(email.body_text)
        for name in ("so", "qte", "project_no"):
            keys[name] |= body_keys[name]
        if not matcher.has_keys(keys):
            continue
        override = overrides.get(email.message_id)
        if override == "ignore":
            continue
        local_date = email.date.astimezone().date()
        reply = rules_mod.is_reply(email.subject)
        project, _keys = matcher.match_email(email, index)
        forced = rules_mod.trigger_by_id(rules, override) if override else None
        trigger = forced or (None if reply else rules_mod.classify(email.subject, rules))

        if project is None:
            if trigger and trigger.get("id") == "assignment":
                if cfg.get("auto_create_projects", True):
                    parsed = matcher.parse_assignment_subject(email.subject)
                    project = create_project(cfg, email, parsed, trigger.get("stage", ""), dry_run)
                    if project is not None:
                        projects.append(project)
                        index = matcher.build_index(projects)
                else:
                    parsed = matcher.parse_assignment_subject(email.subject)
                    body_parsed = extract.parse_assignment_body(email.body_text)
                    entry = projects_mod.pending_entry(email, parsed, body_parsed)
                    existing = pending.get(email.message_id)
                    if existing is None or existing.get("status") != "dismissed":
                        pending[email.message_id] = entry
            if project is None:
                reason = "no matching project" if trigger else "no matching project, no trigger"
                unmatched.append(unmatched_entry(local_date, email, reason, keys))
                continue

        if reply and forced is None:
            continue

        if trigger is None:
            unmatched.append(unmatched_entry(local_date, email, "no trigger", keys))
            continue

        trigger_id = trigger["id"]
        if trigger_id == "assignment" and not project.project_no:
            candidates = sorted(keys["project_no"])
            if candidates:
                project.project_no = candidates[0]
        bucket = triggers.setdefault(project.key, {})
        if trigger_id == "assignment":
            bucket[trigger_id] = min(bucket.get(trigger_id, local_date), local_date)
        else:
            bucket[trigger_id] = max(bucket.get(trigger_id, local_date), local_date)
        matched.setdefault(project.key, []).append((email, trigger_id, reply))

    for project in projects:
        saved = stored.get(project.key) or {}
        bucket = triggers.setdefault(project.key, {})
        for trigger_id, value in saved.items():
            if trigger_id in bucket:
                continue
            try:
                bucket[trigger_id] = date.fromisoformat(str(value))
            except ValueError:
                continue
        if not bucket:
            triggers.pop(project.key, None)

    return triggers, unmatched, prune_pending(pending, projects), matched


def slug_for(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")[:40]
    digest = hashlib.sha1((name or "").encode("utf-8")).hexdigest()[:6]
    return "%s-%s" % (slug or "step", digest)


def pm_tasks(project: Project, rows: list[dict], include: str, prefix: str) -> list[Task]:
    pattern = re.compile(include, re.I) if include else None
    tasks: list[Task] = []
    for row in rows:
        name = row.get("name") or ""
        if not name:
            continue
        if not (row.get("mine") or (pattern and pattern.search(name))):
            continue
        finish = row.get("finish")
        start = row.get("start")
        tasks.append(
            Task(
                id="%s-pm-%s" % (prefix, slug_for(name)),
                title=extract.strip_project_prefix(name),
                stage=notes.PM_HEADING,
                due=date.fromisoformat(finish) if finish else None,
                trigger_date=date.fromisoformat(start) if start else None,
                project_no=project.project_no,
                source="tracker",
            )
        )
    return tasks


def build_tasks(
    project: Project,
    triggers: dict[str, date],
    rules: dict,
    holidays: list[date],
    today: date,
    schedule: list[dict] | None = None,
    pm_include: str = "",
) -> tuple[list[Task], str]:
    prefix = task_prefix(project)
    tasks: list[Task] = []
    stage = "Unknown"
    latest = None
    for trigger_id, trigger_date in triggers.items():
        trigger = rules_mod.trigger_by_id(rules, trigger_id)
        if trigger is None:
            continue
        if latest is None or trigger_date > latest:
            latest = trigger_date
            stage = trigger.get("stage") or stage
    for step in rules_mod.workflow_steps(rules):
        trigger_date = triggers.get(step.get("trigger") or "")
        offset = step.get("offset")
        due = None
        if trigger_date is not None and offset is not None:
            due = rules_mod.add_business_days(trigger_date, int(offset), holidays)
        tasks.append(
            Task(
                id="%s-%s" % (prefix, step["id"]),
                title=step.get("title", step["id"]),
                stage=step.get("stage", ""),
                due=due,
                trigger_date=trigger_date,
                project_no=project.project_no,
                source="tracker",
            )
        )
    tasks.extend(pm_tasks(project, schedule or [], pm_include, prefix))
    return tasks, stage


def merge_block_state(tasks: list[Task], body: str) -> dict:
    entries, _user = notes.parse_block(body)
    for task in tasks:
        entry = entries.get(task.id)
        if entry and entry["mark"] != " ":
            task.done = True
            task.done_date = entry["done_date"]
            task.mark = entry["mark"]
    return entries


def iso_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def apply_due_overrides(
    tasks: list[Task],
    entries: dict,
    overrides: dict,
    computed: dict,
) -> dict:
    dues: dict[str, str | None] = {}
    for task in tasks:
        dues[task.id] = task.due.isoformat() if task.due else None
        override = overrides.get(task.id)
        if override is None:
            entry = entries.get(task.id)
            if entry and entry["mark"] == " " and task.id in computed and entry["due"] != iso_date(computed[task.id]):
                override = {"due": entry["due"].isoformat() if entry["due"] else None, "note": ""}
                overrides[task.id] = override
        if override:
            task.due = iso_date(override.get("due"))
            task.note = override.get("note") or ""
    return dues


def latest_kickoff(items: list[tuple[Email, str, bool]]) -> Email | None:
    latest = None
    for email, trigger_id, reply in items:
        if reply or trigger_id != "kickoff":
            continue
        if latest is None or email.date > latest.date:
            latest = email
    return latest


def project_info(matched: dict) -> dict[str, dict]:
    info: dict[str, dict] = {}
    for key, items in matched.items():
        assignment = None
        kickoff = None
        for email, trigger_id, reply in items:
            if reply:
                continue
            if trigger_id == "assignment" and (assignment is None or email.date < assignment.date):
                assignment = email
            if trigger_id == "kickoff" and (kickoff is None or email.date > kickoff.date):
                kickoff = email
        entry = {
            "sharepoint": "",
            "cpr": "",
            "rmt": "",
            "ship_date": "",
            "design_need_by": "",
            "team": [],
            "links": [],
        }
        if assignment is not None:
            parsed = extract.parse_assignment_body(assignment.body_text)
            entry["sharepoint"] = parsed["sharepoint"]
            entry["team"] = parsed["team"]
        if kickoff is not None:
            parsed = extract.parse_kickoff_body(kickoff.body_text)
            entry["sharepoint"] = entry["sharepoint"] or parsed["project_page"]
            entry["cpr"] = parsed["cpr"]
            entry["rmt"] = parsed["rmt"]
            entry["ship_date"] = parsed["ship_date"] or ""
            entry["design_need_by"] = parsed["design_need_by"] or ""
        links: list[dict] = []
        seen: set[str] = set()
        for title, url in (
            ("Project page", entry["sharepoint"]),
            ("CPR", entry["cpr"]),
            ("Risk & Issues document", entry["rmt"]),
        ):
            if url and url not in seen:
                seen.add(url)
                links.append({"title": title, "url": url})
        for email, _trigger, _reply in items:
            for link in extract.extract_links(email.body_text):
                if "/:p:/" not in link["url"] and ".pptx" not in link["url"].lower():
                    continue
                if link["url"] in seen:
                    continue
                seen.add(link["url"])
                links.append(link)
        entry["links"] = links
        info[key] = entry
    return info


def ape_sheet_index(sheet: dict | None) -> dict:
    index: dict[str, dict] = {}
    ranks: dict[str, tuple] = {}
    my_initials = str((sheet or {}).get("my_initials") or "")
    for row in (sheet or {}).get("rows") or []:
        project_id = str(row.get("project_id") or "")
        if not project_id:
            continue
        rank = ape_sheet._rank(row, my_initials)
        for bucket in ("so", "qte", "project_no"):
            for value in (row.get("keys") or {}).get(bucket) or ():
                key = str(value)
                if not key:
                    continue
                if key in ranks and ranks[key] <= rank:
                    continue
                ranks[key] = rank
                index[key] = {
                    "project_id": project_id,
                    "customer": str(row.get("customer") or ""),
                    "so": str(row.get("so") or ""),
                    "tab": str(row.get("tab") or ""),
                }
    return index


def build_snapshot(
    today: date,
    projects: list[Project],
    triggers: dict,
    matched: dict,
    info: dict,
    pending: dict,
    unmatched: list[dict],
    generated: str,
    sheet: dict | None = None,
) -> dict:
    sheet_rows = {}
    if sheet and sheet.get("ok"):
        sheet_rows = ape_sheet.match(
            sheet.get("rows") or [], projects, sheet.get("my_initials") or ""
        )
    rows = []
    for project in projects:
        entry = info.get(project.key) or {}
        sheet_row = sheet_rows.get(project.key) or {}
        ship_date = entry.get("ship_date", "")
        ship_source = "email" if ship_date else ""
        if not ship_date and sheet_row.get("ship_date"):
            ship_date = sheet_row["ship_date"]
            ship_source = "sheet"
        sheet_mismatch = bool(
            sheet_row and ape_sheet.stage_mismatch(sheet_row.get("so_status", ""), project.stage)
        )
        tasks = [
            {
                "id": task.id,
                "title": task.title,
                "stage": task.stage,
                "start": task.trigger_date.isoformat() if task.trigger_date else None,
                "due": task.due.isoformat() if task.due else None,
                "status": task.status,
                "mark": task.mark,
                "source": task.source,
            }
            for task in project.tasks
        ]
        emails = []
        items = sorted(matched.get(project.key) or [], key=lambda item: item[0].date, reverse=True)
        for email, trigger_id, reply in items[:50]:
            entry = {
                "date": email.date.astimezone().isoformat(timespec="seconds"),
                "sender": email.sender,
                "subject": email.subject,
                "trigger": trigger_id,
                "reply": reply,
                "message_id": email.message_id,
                "open": open_info(email),
            }
            if email.message_id.startswith("calendar:"):
                entry["minutes"] = calendar_minutes(email)
                entry["start"] = email.date.astimezone().isoformat(timespec="seconds")
            emails.append(entry)
        rows.append(
            {
                "key": project.key,
                "name": project.name,
                "folder": project.folder,
                "note_path": project.note_path,
                "customer": project.customer,
                "project_no": project.project_no,
                "so": list(project.so),
                "qte": list(project.qte),
                "level": project.level,
                "stage": project.stage,
                "assigned": project.assigned,
                "ship_date": ship_date,
                "ship_source": ship_source,
                "sheet": sheet_row,
                "sheet_mismatch": sheet_mismatch,
                "design_need_by": entry.get("design_need_by", ""),
                "sharepoint": entry.get("sharepoint", ""),
                "cpr": entry.get("cpr", ""),
                "rmt": entry.get("rmt", ""),
                "team": list(entry.get("team") or []),
                "links": list(entry.get("links") or []),
                "triggers": {
                    trigger_id: value.isoformat()
                    for trigger_id, value in (triggers.get(project.key) or {}).items()
                },
                "tasks": tasks,
                "emails": emails,
            }
        )
    return {
        "generated": generated,
        "today": today.isoformat(),
        "projects": rows,
        "pending": list(pending.values()),
        "unmatched": unmatched,
        "ape_sheet": {
            "ok": bool((sheet or {}).get("ok")),
            "error": str((sheet or {}).get("error") or ""),
            "source": str((sheet or {}).get("source") or ""),
            "source_mtime": str((sheet or {}).get("source_mtime") or ""),
            "source_mtime_label": str((sheet or {}).get("source_mtime_label") or ""),
            "kind": str((sheet or {}).get("kind") or ""),
            "warning": str((sheet or {}).get("warning") or ""),
            "copy": str((sheet or {}).get("copy") or ""),
            "url": str((sheet or {}).get("url") or ""),
            "stale": bool((sheet or {}).get("stale")),
            "stale_hours": int((sheet or {}).get("stale_hours") or 48),
            "row_count": len((sheet or {}).get("rows") or []),
            "matched": len(sheet_rows),
        },
        "ape_sheet_index": ape_sheet_index(sheet) if (sheet or {}).get("ok") else {},
    }


def summary_lines(
    projects: list[Project],
    unmatched: list[dict],
    today: date,
    due_soon_days: int,
) -> list[str]:
    rows = []
    for project in projects:
        for task in project.tasks:
            rows.append((task.due, project, task))
    rows.sort(key=lambda r: (r[0] is None, r[0] or date.max, r[1].name.lower()))

    def fmt(due, project, task):
        when = due.isoformat() if due else "no date"
        return "  %-10s  %-46s  %s" % (when, task.title[:46], project.name)

    lines: list[str] = []
    overdue = [r for r in rows if r[2].due and not r[2].done and r[2].due < today]
    soon = [r for r in rows if r[2].due and not r[2].done and today <= r[2].due and (r[2].due - today).days <= due_soon_days]
    upcoming = [r for r in rows if r[2].due and not r[2].done and (r[2].due - today).days > due_soon_days]
    no_dated = [p for p in projects if not any(t.due for t in p.tasks)]

    lines.append("OVERDUE (%d)" % len(overdue))
    if overdue:
        lines.extend(fmt(*row) for row in overdue)
    else:
        lines.append("  none")
    lines.append("")
    lines.append("DUE SOON (%d, within %d days)" % (len(soon), due_soon_days))
    if soon:
        lines.extend(fmt(*row) for row in soon)
    else:
        lines.append("  none")
    lines.append("")
    lines.append("UPCOMING (%d)" % len(upcoming))
    if upcoming:
        lines.extend(fmt(*row) for row in upcoming)
    else:
        lines.append("  none")
    lines.append("")
    lines.append("PROJECTS WITHOUT ANY DATED TASK (%d)" % len(no_dated))
    if no_dated:
        lines.extend("  " + p.name for p in sorted(no_dated, key=lambda p: p.name.lower()))
    else:
        lines.append("  none")
    lines.append("")
    lines.append("UNMATCHED EMAILS (%d)" % len(unmatched))
    if unmatched:
        lines.extend(
            "  %s  %s  [%s]" % (entry["date"], entry["subject"][:90], entry["reason"]) for entry in unmatched
        )
    else:
        lines.append("  none")
    return lines


def write_log(path: str | Path, lines: list[str]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    block = "\n".join(["=== tracker run %s ===" % stamp] + lines) + "\n\n"
    existing = ""
    if target.is_file():
        existing = target.read_text(encoding="utf-8", errors="replace")
    combined = existing + block
    if len(combined.encode("utf-8")) > LOG_LIMIT:
        combined = combined[-int(LOG_LIMIT * 0.9):]
    target.write_text(combined, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tracker", description="Local project deadline tracker")
    parser.add_argument("--dry-run", action="store_true", help="report changes without writing anything")
    parser.add_argument("--since", type=int, default=120, help="only read emails newer than N days")
    parser.add_argument("--config", default=str(HERE / "config.yaml"), help="path to config.yaml")
    parser.add_argument("--no-dashboard", action="store_true", help="skip dashboard.html")
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    cfg = load_config(config_path)
    with locking.file_lock(locking.lock_path_for(cfg)):
        return run(args, cfg, config_path)


def run(args: argparse.Namespace, cfg: dict, config_path: Path) -> int:
    rules_path = Path(cfg.get("rules_path") or (config_path.parent / "rules.yaml"))
    if not rules_path.is_file():
        rules_path = HERE / "rules.yaml"
    rules = rules_mod.load_rules(rules_path)
    holidays = rules_mod.parse_holidays(cfg.get("holidays") or [])
    today = date.today()
    due_soon_days = int(cfg.get("due_soon_days") or 3)
    state_path = Path(cfg.get("state_path") or (HERE / "state.json"))
    log_path = Path(cfg.get("log_path") or (ROOT / "output" / "last_run.log"))

    out("Project deadline tracker%s" % (" (dry run)" if args.dry_run else ""))
    out("vault: %s" % cfg.get("vault"))
    out("today: %s" % today.isoformat())

    projects = matcher.build_projects(cfg.get("vault") or "", cfg.get("exclude_folders") or [])
    index = matcher.build_index(projects)
    emails = collect_all(cfg, since_days=args.since)
    out("projects: %d, emails: %d" % (len(projects), len(emails)))

    stored_state = state_mod.load(state_path)
    triggers, unmatched, pending, matched = collect_trigger_dates(
        cfg, rules, projects, index, emails, stored_state, args.dry_run
    )
    info = project_info(matched)
    sheet = None
    try:
        sheet = ape_sheet.load(cfg)
    except Exception as exc:
        sheet = {"ok": False, "error": str(exc) or exc.__class__.__name__, "rows": [], "names": {}}
    if sheet.get("ok"):
        sheet_matches = ape_sheet.match(
            sheet.get("rows") or [], projects, sheet.get("my_initials") or ""
        )
        out("tracker sheet: %d rows, %d matched" % (len(sheet.get("rows") or []), len(sheet_matches)))
        if not args.dry_run:
            pending.update(
                ape_sheet.sheet_pending(
                    sheet.get("rows") or [],
                    sheet.get("my_initials") or "",
                    projects,
                    pending,
                    datetime.now().astimezone().isoformat(timespec="seconds"),
                )
            )
    else:
        out("tracker sheet: %s" % (sheet.get("error") or "unavailable"))
    stages = rules_mod.workflow_stages(rules)
    me_name = str((cfg.get("me") or {}).get("name") or "")
    pm_include = str(cfg.get("pm_schedule_include") or "")
    due_overrides = dict(stored_state.get("due_overrides") or {})
    computed_due: dict[str, str | None] = {}

    note_changes: list[str] = []
    for project in projects:
        project_triggers = dict(triggers.get(project.key) or {})
        if "assignment" not in project_triggers and project.assigned:
            try:
                project_triggers["assignment"] = date.fromisoformat(project.assigned[:10])
            except ValueError:
                pass
        if not project_triggers:
            continue
        kickoff = latest_kickoff(matched.get(project.key) or [])
        schedule = extract.parse_schedule(kickoff.body_text, me_name) if kickoff is not None else []
        tasks, stage = build_tasks(
            project, project_triggers, rules, holidays, today, schedule=schedule, pm_include=pm_include
        )
        project.stage = stage
        project.triggers = dict(project_triggers)
        body = ""
        if project.note_path and notes.lp(project.note_path).is_file():
            text, _nl, _bom, _enc = notes.read_text(project.note_path)
            _data, _raw, body = notes.split_frontmatter(text)
        entries = merge_block_state(tasks, body)
        computed_due.update(apply_due_overrides(tasks, entries, due_overrides, stored_state.get("computed_due") or {}))
        project.tasks = tasks
        if not project.note_path:
            if cfg.get("auto_create_projects", True):
                out("  would create note for %s" % project.name)
            continue
        project_entry = info.get(project.key) or {}
        updates = {
            "project_no": project.project_no,
            "so": project.so,
            "qte": project.qte,
            "customer": project.customer,
            "level": project.level,
            "stage": stage,
            "assigned": project_triggers.get("assignment", date.min).isoformat()
            if "assignment" in project_triggers
            else project.assigned,
            "sharepoint": project_entry.get("sharepoint", ""),
            "cpr": project_entry.get("cpr", ""),
            "rmt": project_entry.get("rmt", ""),
            "ship_date": project_entry.get("ship_date", ""),
            "design_need_by": project_entry.get("design_need_by", ""),
            "team": project_entry.get("team") or [],
        }
        updates = {k: v for k, v in updates.items() if v not in (None, "", [])}
        changed, diff = notes.apply_update(
            project.note_path, updates, tasks, today, dry_run=args.dry_run, stages=stages
        )
        if changed:
            note_changes.append(project.note_path)
            if args.dry_run:
                out("")
                out("--- would update: %s" % project.note_path)
                out(diff.rstrip("\n"))

    for project in projects:
        if project.tasks:
            continue
        if project.note_path and notes.lp(project.note_path).is_file():
            text, _nl, _bom, _enc = notes.read_text(project.note_path)
            _data, _raw, body = notes.split_frontmatter(text)
            extra = notes.collect_note_tasks(body)
            if extra:
                project.tasks = extra

    ensure_dashboard_note(cfg, args.dry_run)

    lines = summary_lines(projects, unmatched, today, due_soon_days)
    out("")
    for line in lines:
        out(line)

    if note_changes:
        out("")
        out("notes %s: %d" % ("that would change" if args.dry_run else "updated", len(note_changes)))
        for path in note_changes:
            out("  %s" % path)

    if not args.no_dashboard:
        target = Path((cfg.get("dashboard") or {}).get("output") or (ROOT / "output" / "dashboard.html"))
        if args.dry_run:
            out("")
            out("would write dashboard: %s" % target)
        else:
            dashboard.write(
                target,
                projects,
                unmatched,
                today,
                due_soon_days,
                datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
                (cfg.get("dashboard") or {}).get("plotly_js") or "cdn",
            )
            out("")
            out("dashboard written: %s" % target)

    if args.dry_run:
        out("")
        out("dry run: no files were written")
    else:
        generated = datetime.now().astimezone().isoformat(timespec="seconds")
        state_data = dict(stored_state)
        state_data.update(
            {
                "processed": sorted(set(stored_state.get("processed") or []) | set(e.message_id for e in emails)),
                "projects": {
                    key: {tid: value.isoformat() for tid, value in bucket.items()}
                    for key, bucket in triggers.items()
                },
                "pending": pending,
                "email_overrides": stored_state.get("email_overrides") or {},
                "due_overrides": due_overrides,
                "computed_due": computed_due,
                "last_run": generated,
            }
        )
        state_mod.save(state_path, state_data)
        write_log(log_path, lines)
        out("state written: %s" % state_path)
        out("log appended: %s" % log_path)
        snapshot_path = Path(cfg.get("snapshot_path") or (ROOT / "output" / "snapshot.json"))
        snapshot = build_snapshot(
            today, projects, triggers, matched, info, pending, unmatched, generated, sheet=sheet
        )
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
        out("snapshot written: %s" % snapshot_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
