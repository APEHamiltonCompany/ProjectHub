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
from tracker import team
from tracker.models import Email, Project, Task
from tracker.sources import collect_all
from tracker.sources import ape_sheet
from tracker.sources import ms_project

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
LOG_LIMIT = 1024 * 1024


def out(text: str = "") -> None:
    sys.stdout.write(text.encode("ascii", "replace").decode("ascii") + "\n")


def load_config(path: str | Path) -> dict:
    if not Path(path).is_file():
        raise SystemExit("Config not found: %s\nRun setup.cmd to create it." % path)
    with open(path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    cfg = settings_mod.apply_overlay(cfg, settings_mod.load_overlay(settings_mod.settings_path_for(path)))
    return settings_mod.resolve_drives(settings_mod.go_offline(cfg))


def render_template(text: str, values: dict) -> str:
    result = text
    for key, value in values.items():
        result = result.replace("{" + key + "}", str(value))
    return result


def yaml_list(values: list[str]) -> str:
    return ", ".join('"%s"' % v for v in values)


def task_prefix(project: Project) -> str:
    """Task ids start with this. id_prefix (frontmatter) keeps the ids stable when an SO is added to a QTE project."""
    return project.id_prefix or projects_mod.id_prefix_for(project.project_no, project.key)


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


AUTO_CREATED_LIMIT = 200


def creation_block(cfg: dict, projects: list[Project], previous_count: int) -> str:
    """Why automatic project creation is off for this run ("" = allowed). A scan that suddenly finds far fewer
    folders than the last run (R: half-reachable, wrong vault) must never create projects: that makes duplicates."""
    if not cfg.get("auto_create_projects"):
        return "switched off"
    me = cfg.get("me") or {}
    if not str(me.get("name") or "").strip() and not str(me.get("email") or "").strip():
        return "your name and email are not set, so the To: line cannot be checked"
    if (cfg.get("offline") or {}).get("active"):
        return "working from the backup vault"
    if previous_count and not projects:
        return "the vault scan found no projects (last run: %d)" % previous_count
    if previous_count >= 5 and len(projects) < previous_count * 0.8:
        return "the vault scan found %d projects, the last run had %d" % (len(projects), previous_count)
    return ""


def old_archive_numbers(cfg: dict) -> set[str]:
    """SO/QTE/020 numbers of folders moved to <vault>/_Archive by older versions. The scan skips _Archive, so
    without this an assignment email for an archived project would make a duplicate folder."""
    root = Path(cfg.get("vault") or "") / matcher.ARCHIVE_DIR
    numbers: set[str] = set()
    for folder, _customer in matcher.project_dirs(root, [], matcher.folder_layout(cfg)):
        keys = matcher.extract_keys(folder.name)
        numbers |= keys["so"] | keys["qte"] | keys["project_no"]
    return numbers


def entry_numbers(entry: dict) -> set[str]:
    parsed = entry.get("parsed") or {}
    return set(parsed.get("so") or []) | set(parsed.get("qte") or []) | ({parsed["project_no"]} if parsed.get("project_no") else set())


def auto_create_project(cfg: dict, entry: dict, dry_run: bool) -> Project | None:
    try:
        info = projects_mod.create_from_pending(cfg, entry, dry_run=dry_run)
    except (OSError, ValueError) as exc:
        out("  cannot create project: %s" % exc)
        return None
    out("  %s project folder: %s" % ("would create" if dry_run else "created", info["folder"]))
    parsed = entry.get("parsed") or {}
    return Project(
        folder=info["folder"],
        name=Path(info["folder"]).name,
        note_path=None if dry_run else info["note_path"],
        customer=parsed.get("customer", ""),
        project_no=parsed.get("project_no", ""),
        so=list(parsed.get("so") or []),
        qte=list(parsed.get("qte") or []),
        level=parsed.get("level", ""),
        stage="Pre-Kickoff",
    )


def apply_learned_keys(projects: list[Project], learned: dict) -> None:
    for project in projects:
        extra = learned.get(project.key) or {}
        for name in ("so", "qte"):
            values = getattr(project, name)
            values.extend(value for value in extra.get(name) or [] if value not in values)


def learn_keys(projects: list[Project], emails: list[Email], rules: dict, learned: dict) -> list[str]:
    """Folders named only by their 020 number pick up the SO/QTE numbers of their assignment email, so later
    emails that quote only the SO still match. A number already used by another folder is never taken, and a
    020 number shared by several folders teaches nothing."""
    by_number: dict[str, list[Project]] = {}
    for project in projects:
        if project.project_no:
            by_number.setdefault(project.project_no, []).append(project)
    owned = {name: {value for p in projects for value in getattr(p, name)} for name in ("so", "qte")}
    added: list[str] = []
    for email in sorted(emails, key=lambda e: e.date):
        if rules_mod.is_reply(email.subject):
            continue
        trigger = rules_mod.classify(email.subject, rules)
        if not trigger or trigger.get("id") != "assignment":
            continue
        parsed = matcher.parse_assignment_subject(email.subject)
        owners = by_number.get(parsed.get("project_no") or "") or []
        if len(owners) != 1:
            continue
        project = owners[0]
        for name in ("so", "qte"):
            for value in parsed.get(name) or []:
                if value in owned[name]:
                    continue
                owned[name].add(value)
                getattr(project, name).append(value)
                bucket = learned.setdefault(project.key, {})
                bucket.setdefault(name, []).append(value)
                added.append("%s: %s" % (project.name, value))
    return added


def number_conflicts(projects: list[Project], vault: str | Path) -> list[dict]:
    """SO/QTE/020 numbers that more than one folder claims; emails for them go to whichever folder is found first."""
    owners: dict[str, list[Project]] = {}
    for project in projects:
        numbers = set(project.so) | set(project.qte) | ({project.project_no} if project.project_no else set())
        for number in numbers:
            owners.setdefault(number, []).append(project)
    return [
        {"number": number, "folders": sorted(matcher.relative_folder(vault, p.folder) for p in found)}
        for number, found in sorted(owners.items())
        if len(found) > 1
    ]


def collect_trigger_dates(
    cfg: dict,
    rules: dict,
    projects: list[Project],
    index: dict,
    emails: list[Email],
    state: dict,
    dry_run: bool,
    created: list[dict] | None = None,
    archive_numbers: set[str] | None = None,
    related: dict | None = None,
) -> tuple[dict, list[dict], dict, dict]:
    """created: a list to collect automatically created projects in; None = assignments wait in the hub.
    archive_numbers: numbers of old _Archive folders; their assignments always wait in the hub.
    related: a dict to collect every email matched to a project in ({key: [Email]}), replies and
    emails without a trigger included; used only to look for links."""
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
        project = subject_project(email.subject, index) or matcher.match_email(email, index)[0]
        forced = rules_mod.trigger_by_id(rules, override) if override else None
        trigger = forced or (None if reply else rules_mod.classify(email.subject, rules))

        if project is None:
            if trigger and trigger.get("id") == "assignment" and matcher.addressed_to_me(email.to, cfg.get("me")) is False:
                continue
            if trigger and trigger.get("id") == "assignment":
                parsed = matcher.parse_assignment_subject(email.subject)
                body_parsed = extract.parse_assignment_body(email.body_text)
                entry = projects_mod.pending_entry(email, parsed, body_parsed, matcher.folder_layout(cfg))
                existing = pending.get(email.message_id)
                in_archive = entry_numbers(entry) & (archive_numbers or set())
                if created is not None and in_archive and (existing is None or existing.get("status") == "new"):
                    out("  not created automatically (already in %s: %s): %s" % (matcher.ARCHIVE_DIR, ", ".join(sorted(in_archive)), email.subject))
                elif created is not None and (existing is None or existing.get("status") == "new"):
                    project = auto_create_project(cfg, entry, dry_run)
                    if project is not None:
                        pending.pop(email.message_id, None)
                        projects.append(project)
                        index = matcher.build_index(projects)
                        created.append(
                            {
                                "key": project.key,
                                "folder": matcher.relative_folder(cfg.get("vault") or "", project.folder),
                                "subject": email.subject,
                                "message_id": email.message_id,
                                "at": datetime.now().isoformat(timespec="seconds"),
                            }
                        )
                if project is None and (existing is None or existing.get("status") != "dismissed"):
                    pending[email.message_id] = entry
            if project is None:
                reason = "no matching project" if trigger else "no matching project, no trigger"
                unmatched.append(unmatched_entry(local_date, email, reason, keys))
                continue

        if related is not None:
            related.setdefault(project.key, []).append(email)

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


def sender_name(sender: str) -> str:
    """'Chris Derby </O=EXCHANGELABS/...>' -> 'chris derby'."""
    return re.sub(r"\s+", " ", (sender or "").split("<")[0].replace('"', "")).strip().lower()


def project_ids(project: Project) -> set[str]:
    return set(project.so or ()) | set(project.qte or ()) | ({project.project_no} if project.project_no else set())


def row_ids(row: dict) -> set[str]:
    keys = matcher.extract_keys(row.get("name") or "")
    return keys["so"] | keys["qte"] | keys["project_no"]


def foreign_row(row: dict, ids: set[str]) -> bool:
    """A row naming another project's SO/QTE/020 number (a table pasted from a different job)."""
    named = row_ids(row)
    return bool(named) and not (named & ids)


def table_mismatch(rows: list[dict], ids: set[str]) -> set[str]:
    """The table's SO/QTE/020 numbers when none of them is one of ids (wrong table pasted under this subject), else empty.
    One match is enough: a customer often has several SOs on one project. Customer names are never compared."""
    named: set[str] = set()
    for row in rows:
        named |= row_ids(row)
    return named if named and not (named & ids) else set()


def subject_project(subject: str, index: dict) -> Project | None:
    """The project of the first known id in the subject ("Amazon SO-283465 ... (Previously SO-268726)" -> SO-283465)."""
    found: list[tuple[int, Project]] = []
    for name, values in matcher.extract_keys(subject).items():
        for value in values:
            if value in index[name]:
                found.append((subject.find(value.split("-")[-1]), index[name][value]))
    return min(found, key=lambda item: item[0])[1] if found else None


def collect_timelines(
    rules: dict,
    projects: list[Project],
    emails: list[Email],
    state: dict,
    me_name: str = "",
    skipped: list[str] | None = None,
) -> dict[str, list[dict]]:
    """Every readable PM table per project, oldest first. Replies count only for the text above the quoted thread.
    A table whose SO/QTE/020 numbers match neither the subject nor the project is skipped whole (noted in skipped)."""
    index = matcher.build_index(projects)
    overrides = state.get("email_overrides") or {}
    found: dict[str, list[dict]] = {}
    for email in sorted(emails, key=lambda e: e.date):
        body = email.body_text or ""
        if "duration" not in body.lower() or overrides.get(email.message_id) == "ignore":
            continue
        project = subject_project(email.subject, index)
        if project is None and not matcher.has_keys(matcher.extract_keys(email.subject)):
            project, _keys = matcher.match_email(email, index)
        if project is None:
            continue
        reply = rules_mod.is_reply(email.subject)
        subject_keys = matcher.extract_keys(email.subject)
        ids = project_ids(project) | subject_keys["so"] | subject_keys["qte"] | subject_keys["project_no"]
        parsed = extract.parse_schedule(extract.own_text(body) if reply else body, me_name)
        mismatch = table_mismatch(parsed, ids)
        if mismatch:
            if skipped is not None:
                skipped.append(
                    "timeline skipped: %s %s (subject/project %s, table %s)"
                    % (email.date.astimezone().date().isoformat(), email.subject.strip()[:80],
                       ", ".join(sorted(ids)), ", ".join(sorted(mismatch)))
                )
            continue
        rows = [row for row in parsed if not foreign_row(row, ids)]
        if not rows:
            continue
        forced = overrides.get(email.message_id)
        trigger_id = forced or (None if reply else (rules_mod.classify(email.subject, rules) or {}).get("id"))
        kind = "Reply" if reply and not forced else {"kickoff": "KO", "at_notification": "A&T"}.get(trigger_id, "Email")
        local_date = email.date.astimezone().date()
        found.setdefault(project.key, []).append(
            {
                "date": local_date,
                "sender": sender_name(email.sender),
                "kind": kind,
                "reply": reply and not forced,
                "kickoff": trigger_id == "kickoff",
                "rows": rows,
            }
        )
    return found


def trusted_senders(tables: list[dict], pm_name: str = "") -> set[str]:
    """The project's KO senders plus the PM named on the tracker sheet."""
    trusted = {table["sender"] for table in tables if table["kickoff"] and table["sender"]}
    for name in re.split(r"[/,]", pm_name or ""):
        if name.strip():
            trusted.add(re.sub(r"\s+", " ", name).strip().lower())
    return trusted


def merge_timelines(rules: dict, tables: list[dict], pm_name: str = "") -> list[dict]:
    """One row per milestone, each from the newest trusted table that lists it; carries a 'source' like 'A&T 11/16'."""
    trusted = trusted_senders(tables, pm_name)
    merged: dict[str, dict] = {}
    order: list[str] = []
    for table in sorted(tables, key=lambda t: t["date"]):
        if trusted:
            if table["sender"] not in trusted:
                continue
        elif table["reply"]:
            continue
        source = "%s %d/%d" % (table["kind"], table["date"].month, table["date"].day)
        for milestone_id, row in milestone_rows(rules, table["rows"]).items():
            if milestone_id not in merged:
                order.append(milestone_id)
            merged[milestone_id] = dict(row, source=source)
    return [merged[milestone_id] for milestone_id in order]


def previous_projects(path: Path) -> dict[str, dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    rows = data.get("projects") if isinstance(data, dict) else None
    return {str(row.get("key") or ""): row for row in rows or [] if isinstance(row, dict)}


def archived_stub(folder: Path, entry: dict) -> Project:
    note = str(entry.get("note_path") or "")
    return Project(
        folder=str(folder),
        name=folder.name,
        note_path=str(folder / Path(note).name) if note else None,
        customer=str(entry.get("customer") or ""),
        project_no=str(entry.get("project_no") or ""),
        so=list(entry.get("so") or []),
        qte=list(entry.get("qte") or []),
        level=str(entry.get("level") or ""),
        stage=str(entry.get("stage") or "Unknown"),
        stage_manual=str(entry.get("stage_manual") or ""),
        stage_manual_source=str(entry.get("stage_manual_source") or ""),
        so_dismissed=list(entry.get("so_dismissed") or []),
        assigned=str(entry.get("assigned") or ""),
        archived=True,
    )


def scan_projects(cfg: dict, archived: dict, previous: dict[str, dict]) -> tuple[list[Project], dict[str, dict]]:
    """Projects in the vault. Archived folders (state archived_projects) are not opened: they come back from the
    previous snapshot as frozen entries ({key: entry}) so their numbers still match emails and the sheet."""
    vault = cfg.get("vault") or ""
    by_rel = {
        str(info.get("rel") or "").lower(): key
        for key, info in archived.items()
        if isinstance(info, dict) and info.get("rel")
    }
    projects: list[Project] = []
    frozen: dict[str, dict] = {}
    for folder, customer in matcher.project_dirs(vault, cfg.get("exclude_folders") or [], matcher.folder_layout(cfg)):
        key = by_rel.get(matcher.relative_folder(vault, folder).lower())
        entry = previous.get(key) if key else None
        if entry is not None:
            stub = archived_stub(Path(folder), entry)
            frozen[stub.key] = entry
            projects.append(stub)
            continue
        project = matcher.project_from_folder(folder, customer)
        project.archived = project.key in archived
        projects.append(project)
    return projects, frozen


def ensure_notes(projects: list[Project], dry_run: bool) -> list[str]:
    """Give every project folder without a .md note a minimal one (never overwrites)."""
    created: list[str] = []
    for project in projects:
        if project.note_path:
            continue
        try:
            path, new = projects_mod.create_note_for_folder(project.folder, project.customer, dry_run=dry_run)
        except OSError as exc:
            out("  could not create note in %s: %s" % (project.folder, exc))
            continue
        if new:
            created.append(str(path))
        if not dry_run:
            project.note_path = str(path)
    return created


def slug_for(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")[:40]
    digest = hashlib.sha1((name or "").encode("utf-8")).hexdigest()[:6]
    return "%s-%s" % (slug or "step", digest)


def milestone_rows(rules: dict, schedule: list[dict] | None) -> dict[str, dict]:
    """KO chart row for each milestone id (first matching row wins)."""
    found: dict[str, dict] = {}
    for row in schedule or []:
        milestone = rules_mod.milestone_for(rules, extract.strip_project_prefix(row.get("name") or ""))
        if milestone is not None and milestone["id"] not in found:
            found[milestone["id"]] = row
    return found


def add_team_fields(cfg: dict, snapshot: dict, schedule_matches: dict) -> None:
    """Schedule window, PM and owner-mismatch flags per project, for the team status and the dashboard."""
    for row in snapshot.get("projects") or []:
        sp = schedule_matches.get(row.get("key")) or {}
        if sp:
            row["schedule"] = {
                "pm": str(sp.get("pm") or ""),
                "level": str(sp.get("level") or ""),
                "start": str(sp.get("start") or ""),
                "finish": str(sp.get("finish") or ""),
                "people": [str(p.get("name") or "") for p in sp.get("people") or [] if p.get("name")],
            }
        row["owner_flags"] = team.owner_flags(cfg, row, sp.get("people") or [], row.get("schedule_mine"))


def pick_schedule(
    rules: dict, mpp_rows: list[dict], email_rows: list[dict], authoritative: bool = True
) -> tuple[list[dict], str]:
    """Rows to date the checklist from, and where they came from: the schedule unless it has no milestone rows."""
    mpp_rows = list(mpp_rows or [])
    email_rows = list(email_rows or [])
    if authoritative:
        if milestone_rows(rules, mpp_rows):
            return mpp_rows, "schedule"
        return (email_rows, "email") if email_rows else ([], "")
    if milestone_rows(rules, email_rows):
        return email_rows, "email"
    return (mpp_rows, "schedule") if mpp_rows else ([], "")


def build_tasks(
    project: Project,
    triggers: dict[str, date],
    rules: dict,
    holidays: list[date],
    today: date,
    schedule: list[dict] | None = None,
    level: str = "",
    chart_source: str = "",
) -> tuple[list[Task], str]:
    """Only the milestone rows of the kickoff timeline are tracked; other PM rows are ignored."""
    prefix = task_prefix(project)
    stage = furthest_stage(triggers, rules)
    chart = milestone_rows(rules, schedule)
    base_level, _repeat = rules_mod.parse_level(level)
    parents: dict[str, Task] = {}
    emitted: list[Task] = []
    for milestone in rules_mod.milestones(rules):
        row = chart.get(milestone["id"])
        if row is None:
            if milestone.get("optional"):
                listed = [str(value).upper() for value in milestone.get("levels") or []]
                if schedule or not base_level or base_level not in listed:
                    continue
            elif not rules_mod.level_allows(milestone, level):
                continue
        parent = Task(
            id="%s-%s" % (prefix, milestone["id"]),
            title=milestone.get("title", milestone["id"]),
            stage=milestone.get("stage", ""),
            due=iso_date((row or {}).get("finish")),
            trigger_date=iso_date((row or {}).get("start")),
            project_no=project.project_no,
            source="tracker",
            milestone=True,
            due_source=(
                "schedule" if chart_source == "schedule" else str((row or {}).get("source") or chart_source or "chart")
            ) if iso_date((row or {}).get("finish")) else "",
        )
        parents[milestone["id"]] = parent
        if milestone.get("enabled", True) is not False:
            emitted.append(parent)
    emitted_ids = {task.id for task in emitted}
    steps: list[Task] = []
    for step in rules_mod.workflow_steps(rules):
        if not rules_mod.level_allows(step, level):
            continue
        parent = parents.get(step.get("milestone") or "")
        if step.get("milestone") and parent is None:
            continue
        # The kickoff timeline's finish date wins; a trigger email only dates a step whose milestone has no date.
        trigger_date = triggers.get(step.get("trigger") or "")
        offset = step.get("offset")
        due = None
        due_source = ""
        if parent is not None and parent.due is not None:
            due, trigger_date, due_source = parent.due, parent.trigger_date, parent.due_source
        elif trigger_date is not None and offset is not None:
            due = rules_mod.add_business_days(trigger_date, int(offset), holidays)
            due_source = "%s+%s" % (step.get("trigger") or "", offset)
        steps.append(
            Task(
                id="%s-%s" % (prefix, step["id"]),
                title=step.get("title", step["id"]),
                stage=step.get("stage", ""),
                due=due,
                trigger_date=trigger_date,
                project_no=project.project_no,
                source="tracker",
                parent=parent.id if parent is not None and parent.id in emitted_ids else "",
                mandatory=bool(step.get("mandatory")),
                due_source=due_source,
            )
        )
    return order_tasks(emitted, steps), stage


def resolve_level(*values: str) -> str:
    """First readable level from note, tracker sheet, kickoff email: 'L2R', 'L3', or ''."""
    for value in values:
        base, repeat = rules_mod.parse_level(value)
        if base:
            return base + ("R" if repeat else "")
    return ""


def furthest_stage(triggers: dict[str, date], rules: dict) -> str:
    """The furthest bucket any trigger email reached."""
    order = rules_mod.bucket_order(rules)
    stage, best = "Unknown", -2
    for trigger_id in triggers:
        trigger = rules_mod.trigger_by_id(rules, trigger_id)
        if trigger is None:
            continue
        name = str(trigger.get("stage") or "")
        rank = order.index(name) if name in order else -1
        if name and rank > best:
            stage, best = name, rank
    return stage


def order_tasks(parents: list[Task], steps: list[Task]) -> list[Task]:
    """Milestones in rules order, each followed by its steps; loose steps keep their place."""
    children: dict[str, list[Task]] = {}
    for task in steps:
        if task.parent:
            children.setdefault(task.parent, []).append(task)
    pending = list(parents)
    out: list[Task] = []

    def emit_until(parent_id: str) -> None:
        while pending:
            parent = pending.pop(0)
            out.append(parent)
            out.extend(children.get(parent.id) or [])
            if parent.id == parent_id:
                return

    for task in steps:
        if task.parent:
            if any(parent.id == task.parent for parent in pending):
                emit_until(task.parent)
            continue
        out.append(task)
    emit_until("")
    return out


def cascade_done(tasks: list[Task], today: date) -> None:
    """A ticked milestone ticks its steps; a milestone whose steps are all done is ticked."""
    children: dict[str, list[Task]] = {}
    for task in tasks:
        if task.parent:
            children.setdefault(task.parent, []).append(task)
    for parent in tasks:
        kids = children.get(parent.id)
        if not kids:
            continue
        if parent.done:
            for kid in kids:
                if not kid.done:
                    kid.done, kid.mark, kid.done_date = True, parent.mark or "x", parent.done_date or today
        elif all(kid.done for kid in kids):
            parent.done, parent.done_date = True, today
            parent.mark = "x" if any(kid.mark == "x" for kid in kids) else "-"


PM_LINE_TITLE_RE = re.compile(r"^\s*-\s*\[.\]\s*(.*?)\s*(?:\U0001F4C5|\U0001F194|\u2705|$)")


def merge_block_state(tasks: list[Task], body: str, rules: dict | None = None) -> dict:
    entries, _user = notes.parse_block(body)
    for task in tasks:
        entry = entries.get(task.id)
        if entry and entry["mark"] != " ":
            task.done = True
            task.done_date = entry["done_date"]
            task.mark = entry["mark"]
    if rules:
        carry_pm_marks(tasks, entries, rules)
    return entries


def carry_pm_marks(tasks: list[Task], entries: dict, rules: dict) -> None:
    """A ticked old PM schedule row (e.g. PreFabApps) ticks the milestone that replaces it."""
    by_id = {task.id: task for task in tasks}
    for task_id, entry in entries.items():
        if notes.PM_ID_MARK not in task_id or entry["mark"] == " ":
            continue
        match = PM_LINE_TITLE_RE.match(entry["line"])
        milestone = rules_mod.milestone_for(rules, extract.strip_project_prefix(match.group(1) if match else ""))
        if milestone is None:
            continue
        target = by_id.get("%s-%s" % (task_id.split(notes.PM_ID_MARK, 1)[0], milestone["id"]))
        if target is not None and not target.done:
            target.done, target.mark, target.done_date = True, entry["mark"], entry["done_date"]


def apply_schedule_done(
    tasks: list[Task], chart_rows: dict[str, dict], rules: dict, ticked: dict, today: date
) -> dict:
    """Tick the checklist items whose schedule row is 100% complete; never untick and never re-tick."""
    complete = {
        milestone_id: row
        for milestone_id, row in (chart_rows or {}).items()
        if ms_project._pct(row.get("pct")) == 100
    }
    if not complete:
        return ticked
    stages = rules_mod.workflow_stages(rules)
    milestone_stage = {
        str(milestone.get("id") or ""): str(milestone.get("stage") or "")
        for milestone in rules_mod.milestones(rules)
    }
    steps = {str(step.get("id") or ""): step for step in rules_mod.workflow_steps(rules)}

    def stage_rank(name: str) -> int:
        return stages.index(name) if name in stages else -1

    def loose_row(step: dict) -> dict | None:
        rank = stage_rank(str(step.get("stage") or ""))
        qualified = [
            row for milestone_id, row in complete.items() if stage_rank(milestone_stage.get(milestone_id, "")) >= rank
        ]
        if not qualified:
            return None
        return min(qualified, key=lambda row: iso_date(row.get("finish")) or today)

    for task in tasks:
        if task.done or task.mark != " " or task.id in ticked:
            continue
        if task.id.startswith("m-") or notes.PM_ID_MARK in task.id:
            continue
        suffix = task.id.rsplit("-", 1)[-1]
        row = None
        if task.milestone:
            row = complete.get(suffix)
        else:
            step = steps.get(suffix)
            if step is None:
                continue
            milestone_id = str(step.get("milestone") or "")
            row = complete.get(milestone_id) if milestone_id else loose_row(step)
        if row is None:
            continue
        task.done = True
        task.mark = "x"
        task.done_date = iso_date(row.get("finish")) or today
        ticked[task.id] = task.done_date.isoformat()
    return ticked


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
        if override is not None and not override.get("note") and iso_date(override.get("due")) in (
            None, task.due, iso_date(computed.get(task.id))
        ):
            # A blank override, or one that only repeats the computed date, would freeze the date when the PM chart moves.
            overrides.pop(task.id)
            override = None
        if override is None:
            entry = entries.get(task.id)
            if (
                entry
                and entry["mark"] == " "
                and entry["due"] is not None
                and task.id in computed
                and entry["due"] != iso_date(computed[task.id])
                and entry["due"] != task.due
            ):
                override = {"due": entry["due"].isoformat(), "note": ""}
                overrides[task.id] = override
        if override:
            due = iso_date(override.get("due"))
            if due != task.due:
                task.due_source = "edited" if due else ""
            task.due = due
            task.note = override.get("note") or ""
    return dues


def project_info(matched: dict, related: dict | None = None, numbers: dict | None = None) -> dict[str, dict]:
    """related: {key: [Email]} every email matched to the project (replies too), searched for design reviews.
    numbers: {key: set of the project's 020 numbers}."""
    info: dict[str, dict] = {}
    related = related or {}
    numbers = numbers or {}
    for key in set(matched) | set(related):
        items = matched.get(key) or []
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
            "design_review": "",
            "previous_dr": [],
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
            entry["level"] = parsed["level"] or ""
        searched = {id(email): email for email, _trigger, _reply in items}
        searched.update({id(email): email for email in related.get(key) or []})
        entry["design_review"], entry["previous_dr"] = extract.design_reviews(
            list(searched.values()),
            set(numbers.get(key) or ()),
            extract.parse_kickoff_body(kickoff.body_text)["dr_form"] if kickoff is not None else "",
        )
        links: list[dict] = []
        seen: set[str] = set()
        for title, url in (
            ("Project page", entry["sharepoint"]),
            ("CPR", entry["cpr"]),
            ("Current DR", entry["design_review"]),
            *(("Previous DR", url) for url in entry["previous_dr"]),
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


def so_hints(emails: list[Email], projects: list[Project]) -> dict[str, list[dict]]:
    """SO numbers a QTE project may have been converted to: an email subject that names one of the project's
    QTEs and an SO the project doesn't list (and the user hasn't dismissed). Newest email per SO."""
    by_qte: dict[str, Project] = {}
    for project in projects:
        for value in project.qte:
            by_qte.setdefault(value, project)
    found: dict[str, dict[str, dict]] = {}
    for email in sorted(emails, key=lambda e: e.date):
        keys = matcher.extract_keys(email.subject)
        if not keys["so"] or not keys["qte"]:
            continue
        owners = {id(by_qte[value]): by_qte[value] for value in keys["qte"] if value in by_qte}
        for project in owners.values():
            for so in sorted(keys["so"] - set(project.so) - set(project.so_dismissed)):
                found.setdefault(project.key, {})[so] = {
                    "so": so,
                    "subject": email.subject,
                    "date": email.date.astimezone().date().isoformat(),
                    "message_id": email.message_id,
                }
    return {key: sorted(items.values(), key=lambda item: item["so"]) for key, items in found.items()}


def archived_new_emails(items: list, since: str) -> int:
    """Trigger emails (not replies) that arrived after the project was archived."""
    try:
        cutoff = datetime.fromisoformat(since)
    except ValueError:
        return 0
    if cutoff.tzinfo is None:
        cutoff = cutoff.astimezone()
    return sum(1 for email, _trigger, reply in items if not reply and email.date.astimezone() > cutoff)


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
    hints: dict | None = None,
    schedule: dict | None = None,
    schedule_mine: int = 0,
    schedule_matched: int = 0,
    archived: dict | None = None,
    frozen: dict | None = None,
) -> dict:
    archived = archived or {}
    frozen = frozen or {}
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
                "parent": task.parent,
                "milestone": task.milestone,
                "mandatory": task.mandatory,
                "due_source": task.due_source,
            }
            for task in project.tasks
        ]
        emails = []
        items = sorted(matched.get(project.key) or [], key=lambda item: item[0].date, reverse=True)
        for email, trigger_id, reply in items[:50]:
            row = {
                "date": email.date.astimezone().isoformat(timespec="seconds"),
                "sender": email.sender,
                "to": email.to,
                "cc": email.cc,
                "subject": email.subject,
                "trigger": trigger_id,
                "reply": reply,
                "message_id": email.message_id,
                "open": open_info(email),
            }
            if email.message_id.startswith("calendar:"):
                row["minutes"] = calendar_minutes(email)
                row["start"] = email.date.astimezone().isoformat(timespec="seconds")
            emails.append(row)
        if project.key in frozen:
            since = str((archived.get(project.key) or {}).get("at") or "")
            rows.append(
                dict(
                    frozen[project.key],
                    folder=project.folder,
                    note_path=project.note_path,
                    emails=emails or list(frozen[project.key].get("emails") or []),
                    archived=True,
                    archived_at=since,
                    archived_new_emails=archived_new_emails(items, since),
                )
            )
            continue
        archived_extra = {}
        if project.key in archived:
            since = str((archived.get(project.key) or {}).get("at") or "")
            archived_extra = {"archived": True, "archived_at": since, "archived_new_emails": archived_new_emails(items, since)}
        rows.append(
            {
                **archived_extra,
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
                "stage_manual": project.stage_manual,
                "stage_manual_source": project.stage_manual_source,
                "so_hints": list((hints or {}).get(project.key) or []),
                "so_dismissed": list(project.so_dismissed),
                "assigned": project.assigned,
                "ship_date": ship_date,
                "ship_source": ship_source,
                "sheet": sheet_row,
                "sheet_mismatch": sheet_mismatch,
                "schedule_source": project.schedule_source,
                "schedule_mine": project.schedule_mine,
                "schedule_project": project.schedule_project,
                "schedule_unlinked": project.schedule_unlinked,
                "design_need_by": entry.get("design_need_by", ""),
                "sharepoint": entry.get("sharepoint", ""),
                "cpr": entry.get("cpr", ""),
                "design_review": entry.get("design_review", ""),
                "previous_dr": list(entry.get("previous_dr") or []),
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
        "ms_project": {
            "ok": bool((schedule or {}).get("ok")),
            "error": str((schedule or {}).get("error") or ""),
            "stale": bool((schedule or {}).get("stale")),
            "source": str((schedule or {}).get("source") or ""),
            "file_mtime": str((schedule or {}).get("file_mtime") or ""),
            "reader": str((schedule or {}).get("reader") or ""),
            "projects": len((schedule or {}).get("projects") or []),
            "mine": int(schedule_mine),
            "matched": int(schedule_matched),
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
    parser.add_argument("--since", type=int, default=None, help="only read emails newer than N days (default: outlook_com.days_back, else 120)")
    parser.add_argument("--config", default=str(HERE / "config.yaml"), help="path to config.yaml")
    parser.add_argument("--no-dashboard", action="store_true", help="skip dashboard.html")
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    if not config_path.is_file():
        out("No config at %s. Run setup.cmd in the ProjectHub folder first." % config_path)
        return 2
    cfg = load_config(config_path)
    with locking.file_lock(locking.lock_path_for(cfg)):
        return run(args, cfg, config_path)


def run(args: argparse.Namespace, cfg: dict, config_path: Path) -> int:
    rules_path = Path(cfg.get("rules_path") or (config_path.parent / "rules.yaml"))
    if not rules_path.is_file():
        rules_path = HERE / "rules.yaml"
    rules = rules_mod.load_rules(rules_path, cfg)
    rules = rules_mod.apply_checklist(rules, cfg)
    holidays = rules_mod.parse_holidays(cfg.get("holidays") or [])
    today = date.today()
    due_soon_days = int(cfg.get("due_soon_days") or 3)
    state_path = Path(cfg.get("state_path") or (HERE / "state.json"))
    log_path = Path(cfg.get("log_path") or (ROOT / "output" / "last_run.log"))

    out("Project deadline tracker%s" % (" (dry run)" if args.dry_run else ""))
    out("vault: %s" % cfg.get("vault"))
    out("today: %s" % today.isoformat())

    if not notes.lp(cfg.get("vault") or "").is_dir():
        reason = (cfg.get("offline") or {}).get("reason") or "the vault is not reachable"
        out("run cancelled: %s. Nothing was changed." % reason)
        return 3

    stored_state = state_mod.load(state_path)
    snapshot_path = Path(cfg.get("snapshot_path") or (ROOT / "output" / "snapshot.json"))
    archived = dict(stored_state.get("archived_projects") or {})
    previous = previous_projects(snapshot_path)
    projects, frozen = scan_projects(cfg, archived, previous)
    created_notes = ensure_notes(projects, args.dry_run)
    if created_notes:
        out("project notes %s: %d (folders that had no .md note)" % ("that would be created" if args.dry_run else "created", len(created_notes)))
    learned = {key: dict(value) for key, value in (stored_state.get("learned_keys") or {}).items() if isinstance(value, dict)}
    apply_learned_keys(projects, learned)
    emails = collect_all(cfg, since_days=args.since)
    out("projects: %d (%d archived, not read), emails: %d" % (len(projects), len(frozen), len(emails)))
    for line in learn_keys(projects, emails, rules, learned):
        out("learned number from assignment email: %s" % line)
    conflicts = number_conflicts(projects, cfg.get("vault") or "")
    for conflict in conflicts:
        out("number used by %d folders: %s (%s)" % (len(conflict["folders"]), conflict["number"], "; ".join(conflict["folders"])))
    index = matcher.build_index(projects)

    block = creation_block(cfg, projects, len(previous))
    if block and cfg.get("auto_create_projects"):
        out("automatic project creation skipped: %s" % block)
    created: list[dict] | None = None if block else []
    related: dict = {}
    triggers, unmatched, pending, matched = collect_trigger_dates(
        cfg, rules, projects, index, emails, stored_state, args.dry_run, created=created,
        archive_numbers=old_archive_numbers(cfg) if created is not None else None, related=related,
    )
    if created:
        out("projects created automatically: %d" % len(created))
    info = project_info(
        matched, related, {project.key: extract.deck_numbers(project.project_no or "") for project in projects}
    )
    sheet = None
    sheet_matches: dict[str, dict] = {}
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
    schedule_data = None
    schedule_matches: dict[str, dict] = {}
    schedule_mine = 0
    schedule_links = dict(stored_state.get("schedule_links") or {})
    try:
        schedule_data = ms_project.load(cfg)
    except Exception as exc:
        schedule_data = {"ok": False, "error": str(exc) or exc.__class__.__name__, "projects": []}
    if schedule_data.get("ok"):
        schedule_matches = ms_project.match(schedule_data, projects, cfg, links=schedule_links)
        schedule_mine = sum(
            1
            for item in schedule_data.get("projects") or ()
            if any(ms_project.is_me(person, cfg) for person in item.get("people") or ())
        )
        line = "schedule: %d projects, %d mine, %d matched" % (
            len(schedule_data.get("projects") or []),
            schedule_mine,
            len(schedule_matches),
        )
        if schedule_data.get("stale"):
            line += " (stale: %s)" % (schedule_data.get("error") or "cached copy")
        out(line)
        if not args.dry_run:
            pending.update(
                ms_project.schedule_pending(
                    schedule_data,
                    cfg,
                    projects,
                    pending,
                    datetime.now().astimezone().isoformat(timespec="seconds"),
                )
            )
    else:
        out("schedule: %s" % (schedule_data.get("error") or "unavailable"))
    stages = rules_mod.workflow_stages(rules)
    me_name = str((cfg.get("me") or {}).get("name") or "")
    skipped_tables: list[str] = []
    timelines = collect_timelines(rules, projects, emails, stored_state, me_name, skipped_tables)
    for line in skipped_tables:
        out(line)
    due_overrides = dict(stored_state.get("due_overrides") or {})
    schedule_ticked = dict(stored_state.get("schedule_ticked") or {})
    computed_due: dict[str, str | None] = {}

    note_changes: list[str] = []
    for project in projects:
        if project.key in frozen:
            continue
        project_triggers = dict(triggers.get(project.key) or {})
        if "assignment" not in project_triggers and project.assigned:
            try:
                project_triggers["assignment"] = date.fromisoformat(project.assigned[:10])
            except ValueError:
                pass
        sp = schedule_matches.get(project.key)
        project.schedule_unlinked = schedule_links.get(project.key) == "off"
        project.schedule_project = str((sp or {}).get("name") or "")
        if sp and "assignment" not in project_triggers and not project.assigned and sp.get("created"):
            try:
                project_triggers["assignment"] = date.fromisoformat(sp["created"][:10])
            except ValueError:
                pass
        if not project_triggers and not sp:
            continue
        email_rows = merge_timelines(
            rules,
            timelines.get(project.key) or [],
            str((sheet_matches.get(project.key) or {}).get("pm_name") or ""),
        )
        mpp_rows = ms_project.schedule_rows(sp) if sp else []
        schedule, schedule_source = pick_schedule(
            rules, mpp_rows, email_rows, bool((cfg.get("ms_project") or {}).get("authoritative", True))
        )
        project.schedule_source = schedule_source
        project.schedule_mine = (
            any(ms_project.is_me(person, cfg) for person in sp.get("people") or ()) if sp else None
        )
        note_level = project.level
        project.level = resolve_level(
            project.level,
            str((sheet_matches.get(project.key) or {}).get("level") or ""),
            str((info.get(project.key) or {}).get("level") or ""),
        )
        tasks, stage = build_tasks(
            project, project_triggers, rules, holidays, today, schedule=schedule,
            level=project.level, chart_source=schedule_source,
        )
        if project.stage_manual:
            stage = project.stage_manual
        project.stage = stage
        project.triggers = dict(project_triggers)
        body = ""
        if project.note_path and notes.lp(project.note_path).is_file():
            text, _nl, _bom, _enc = notes.read_text(project.note_path)
            _data, _raw, body = notes.split_frontmatter(text)
        entries = merge_block_state(tasks, body, rules)
        if schedule_source == "schedule":
            apply_schedule_done(tasks, milestone_rows(rules, schedule), rules, schedule_ticked, today)
        cascade_done(tasks, today)
        computed_due.update(apply_due_overrides(tasks, entries, due_overrides, stored_state.get("computed_due") or {}))
        project.tasks = tasks
        if not project.note_path or (args.dry_run and not notes.lp(project.note_path).is_file()):
            continue
        project_entry = info.get(project.key) or {}
        try:
            note_data = notes.split_frontmatter(notes.read_text(project.note_path)[0])[0]
        except OSError:
            note_data = {}
        if project_entry:
            project_entry["design_review"] = notes.current_dr(note_data.get("design_review"), project_entry.get("design_review", ""))
        updates = {
            "project_no": project.project_no,
            "so": project.so,
            "qte": project.qte,
            "customer": project.customer,
            "level": note_level,
            "stage": stage,
            "assigned": project_triggers.get("assignment", date.min).isoformat()
            if "assignment" in project_triggers
            else project.assigned,
            "sharepoint": project_entry.get("sharepoint", ""),
            "cpr": project_entry.get("cpr", ""),
            "design_review": project_entry.get("design_review", ""),
            "previous_dr": list(project_entry.get("previous_dr") or []),
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
        if project.tasks or project.key in frozen:
            continue
        if project.note_path and notes.lp(project.note_path).is_file():
            text, _nl, _bom, _enc = notes.read_text(project.note_path)
            _data, _raw, body = notes.split_frontmatter(text)
            extra = notes.collect_note_tasks(body)
            if extra:
                project.tasks = extra

    ensure_dashboard_note(cfg, args.dry_run)

    active = [project for project in projects if not project.archived]
    lines = summary_lines(active, unmatched, today, due_soon_days)
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
                active,
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
                "schedule_ticked": schedule_ticked,
                "last_run": generated,
                "learned_keys": learned,
                "auto_created": (list(stored_state.get("auto_created") or []) + list(created or []))[-AUTO_CREATED_LIMIT:],
            }
        )
        state_mod.save(state_path, state_data)
        write_log(log_path, lines)
        out("state written: %s" % state_path)
        out("log appended: %s" % log_path)
        snapshot = build_snapshot(
            today, projects, triggers, matched, info, pending, unmatched, generated, sheet=sheet,
            hints=so_hints(emails, projects), schedule=schedule_data, schedule_mine=schedule_mine,
            schedule_matched=len(schedule_matches), archived=archived, frozen=frozen,
        )
        snapshot["conflicts"] = conflicts
        snapshot["auto_create"] = {"enabled": bool(cfg.get("auto_create_projects")), "blocked": block if cfg.get("auto_create_projects") else ""}
        add_team_fields(cfg, snapshot, schedule_matches)
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
        out("snapshot written: %s" % snapshot_path)
        try:
            result = team.publish(cfg, snapshot, archived, today)
        except Exception as exc:
            result = {"ok": False, "skipped": str(exc) or exc.__class__.__name__}
        if result.get("ok"):
            out("team status published: %s (%d projects)" % (result["folder"], result["projects"]))
        elif team.conf(cfg).get("publish"):
            out("team status not published: %s" % result.get("skipped"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
