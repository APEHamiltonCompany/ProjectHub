from __future__ import annotations

import os
import re
from datetime import date, datetime
from pathlib import Path

from . import matcher, notes, project_files
from . import rules as rules_mod
from .models import Email, Project

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

CHECKLIST_PLACEHOLDER = "{checklist}"
DEFAULT_CHECKLIST_RE = re.compile(r"\{checklist\}\n((?:- \[ \][^\n]*\n(?:[ \t]*\n)*)+)")


def yaml_list(values: list[str]) -> str:
    return ", ".join('"%s"' % v for v in values)


def render_note(template: str, values: dict, checklist: list[str] | None = None) -> str:
    text = template
    if not values.get("sharepoint"):
        text = text.replace("**Links:** [SharePoint project page]({sharepoint})", "**Links:**")
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    if checklist:
        rendered = "\n".join("- [ ] " + item for item in checklist)
        text = DEFAULT_CHECKLIST_RE.sub(lambda _m: rendered + "\n", text, count=1)
    else:
        text = text.replace(CHECKLIST_PLACEHOLDER + "\n", "")
    return text


def suggested_folder(cfg: dict, pending: dict) -> str:
    parsed = pending.get("parsed") or {}
    if not any(parsed.get(key) for key in ("customer", "so", "qte", "project_no")):
        return str(pending.get("suggested_folder") or "")
    return matcher.folder_name_for(
        parsed.get("customer", ""),
        list(parsed.get("so") or []),
        list(parsed.get("qte") or []),
        parsed.get("project_no", ""),
        matcher.folder_layout(cfg),
    )


def _existing_child(parent: Path, name: str) -> Path:
    """parent/name, reusing an existing folder whose name differs only in case."""
    try:
        for entry in notes.lp(parent).iterdir():
            if entry.is_dir() and entry.name.lower() == name.lower():
                return parent / entry.name
    except OSError:
        pass
    return parent / name


def pending_entry(email: Email, parsed: dict, body_parsed: dict, layout: str = matcher.LAYOUT_FLAT) -> dict:
    return {
        "message_id": email.message_id,
        "subject": email.subject,
        "received": email.date.astimezone().isoformat(timespec="seconds"),
        "parsed": {
            "customer": parsed.get("customer", ""),
            "so": list(parsed.get("so") or []),
            "qte": list(parsed.get("qte") or []),
            "project_no": parsed.get("project_no", ""),
            "level": parsed.get("level", ""),
            "description": parsed.get("description", ""),
        },
        "sharepoint": body_parsed.get("sharepoint", ""),
        "team": list(body_parsed.get("team") or []),
        "checklist": list(body_parsed.get("checklist") or []),
        "suggested_folder": matcher.folder_name_for(
            parsed.get("customer", ""),
            list(parsed.get("so") or []),
            list(parsed.get("qte") or []),
            parsed.get("project_no", ""),
            layout,
        ),
        "status": "new",
    }


def mark_pending(state: dict, message_id: str, status: str) -> None:
    pending = state.setdefault("pending", {})
    entry = pending.get(message_id)
    if entry is None:
        return
    entry["status"] = status


def _inside(vault: Path, folder: Path) -> bool:
    base = os.path.abspath(str(vault))
    target = os.path.abspath(str(folder))
    return target != base and target.startswith(base + os.sep)


def _template_values(entry: dict, when: date, stage: str) -> dict:
    parsed = entry.get("parsed") or {}
    so = list(parsed.get("so") or [])
    qte = list(parsed.get("qte") or [])
    team = list(entry.get("team") or [])
    return {
        "date_ddMonYYYY": when.strftime("%d%b%Y"),
        "subject": matcher.strip_assignment_prefix(entry.get("subject", "")),
        "customer": parsed.get("customer", ""),
        "so_list": ", ".join(so),
        "qte_list": ", ".join(qte),
        "so_yaml": yaml_list(so),
        "qte_yaml": yaml_list(qte),
        "project_no": parsed.get("project_no", ""),
        "level": parsed.get("level", ""),
        "description": parsed.get("description", ""),
        "stage": stage,
        "assigned": when.isoformat(),
        "sharepoint": entry.get("sharepoint", ""),
        "cpr": "",
        "rmt": "",
        "ship_date": "",
        "design_need_by": "",
        "team": ", ".join(team),
        "team_yaml": yaml_list(team),
    }


def _received_date(entry: dict) -> date:
    try:
        return datetime.fromisoformat(str(entry.get("received"))).date()
    except (TypeError, ValueError):
        return date.today()


def create_from_pending(
    cfg: dict,
    pending: dict,
    folder_name: str | None = None,
    dry_run: bool = False,
    choices: dict | None = None,
) -> dict:
    """choices: {group: item id | "none"} picked in the hub; None (automatic creation) copies only the
    always-copied files and adds a task to pick the rest on the project page."""
    picked: dict[str, str] | None = None
    if choices is not None:
        picked, errors = project_files.validate_choices(cfg, choices)
        if errors:
            raise ValueError("; ".join(errors))
    vault = Path(cfg.get("vault") or "")
    raw = folder_name or suggested_folder(cfg, pending)
    parts = matcher.split_folder(raw)
    if not parts:
        raise ValueError("invalid folder name: %r" % raw)
    name = parts[-1]
    parent = _existing_child(vault, parts[0]) if len(parts) == 2 else vault
    folder = parent / name
    if not _inside(vault, folder):
        raise ValueError("folder resolves outside the vault: %s" % folder)
    if notes.lp(folder).exists():
        raise ValueError("folder already exists: %s" % folder)
    note_path = folder / (name + ".md")
    template_path = Path(cfg.get("templates_dir") or (ROOT / "templates")) / "Project Template.md"
    if not template_path.is_file():
        raise ValueError("template missing: %s" % template_path)
    when = _received_date(pending)
    parsed = pending.get("parsed") or {}
    project = Project(
        folder=str(folder),
        name=name,
        note_path=str(note_path),
        customer=parsed.get("customer", ""),
        project_no=parsed.get("project_no", ""),
        so=list(parsed.get("so") or []),
        qte=list(parsed.get("qte") or []),
        level=parsed.get("level", ""),
        stage="Pre-Kickoff",
    )
    if dry_run:
        return {"folder": str(folder), "note_path": str(note_path), "files_created": [], "files_skipped": []}

    files_created: list[str] = []
    notes.lp(folder).mkdir(parents=True, exist_ok=True)
    values = _template_values(pending, when, project.stage)
    template = template_path.read_text(encoding="utf-8")
    notes.lp(note_path).write_bytes(
        render_note(template, values, list(pending.get("checklist") or [])).encode("utf-8")
    )
    files_created.append(str(note_path))

    tokens = project_files.tokens_for(project.customer, project.so, project.qte, project.project_no)
    copied, skipped = project_files.copy_files(
        cfg, folder, tokens, project_files.selected_items(cfg, picked)
    )
    files_created.extend(copied)

    rules_path = Path(cfg.get("rules_path") or (HERE / "rules.yaml"))
    rules = rules_mod.load_rules(rules_path, cfg)
    rules = rules_mod.apply_checklist(rules, cfg)
    holidays = rules_mod.parse_holidays(cfg.get("holidays") or [])
    from .tracker import build_tasks

    tasks, stage = build_tasks(project, {"assignment": when}, rules, holidays, date.today())
    project.stage = stage
    notes.apply_update(
        note_path,
        {},
        tasks,
        date.today(),
        stages=rules_mod.workflow_stages(rules),
    )
    _record_file_choices(cfg, note_path, picked)
    pending["status"] = "created"
    return {
        "folder": str(folder),
        "note_path": str(note_path),
        "files_created": files_created,
        "files_skipped": skipped,
    }


def _record_file_choices(cfg: dict, note_path: str | Path, picked: dict[str, str] | None) -> None:
    """Store the hub's picks in the note, or add a task to pick them when created automatically."""
    if not project_files.specs(cfg)[1]:
        return
    from .hub import tasks as tasks_mod

    text, newline, bom, encoding = notes.read_text(note_path)
    if picked:
        data, _raw, body = notes.split_frontmatter(text)
        data = notes.merge_frontmatter(data, project_files.frontmatter_values(cfg, picked))
        text = notes.dump_frontmatter(data) + body
    elif picked is None:
        line = tasks_mod.manual_line(project_files.pick_message(cfg), date.today(), tasks_mod.new_manual_id())
        text = tasks_mod.insert_manual(text, [line])
    notes.write_text(note_path, text, newline, bom, encoding)


STAGE_MANUAL_KEY = "stage_manual"
UNKNOWN_STAGE = "Unknown"


def note_path_for_folder(folder: str | Path) -> Path:
    folder = Path(folder)
    return folder / (folder.name + ".md")


def create_note_for_folder(folder: str | Path, customer: str = "", dry_run: bool = False) -> tuple[Path, bool]:
    """Minimal project note for an existing folder that has none: (path, created). Never overwrites.

    The customer is the customer folder (customer/so layout) or the start of the folder name; SO, QTE and
    project number come from the folder name. Stage starts as Unknown and there are no workflow tasks
    until matching emails arrive."""
    folder = Path(folder)
    existing = matcher.select_note(folder)
    if existing:
        return Path(existing), False
    project = matcher.project_from_folder(folder, customer)
    path = note_path_for_folder(folder)
    if dry_run:
        return path, True
    data = {
        "project_no": project.project_no,
        "so": project.so,
        "qte": project.qte,
        "customer": project.customer,
        "level": "",
        "stage": UNKNOWN_STAGE,
        "assigned": "",
        "sharepoint": "",
        "cpr": "",
        "rmt": "",
        "ship_date": "",
        "design_need_by": "",
        "team": [],
    }
    text = notes.dump_frontmatter(data) + "\n**TO DO:** (Mark done items with #)\n"
    with open(notes.lp(path), "x", encoding="utf-8", newline="\r\n") as fh:
        fh.write(text)
    return path, True


def set_manual_stage(note_path: str | Path, stage: str, source: str = "") -> None:
    """Pin the project's bucket (wins over emails); an empty stage clears the pin. source records who set it
    (empty = by hand, "tracker sheet" = Sync to Project Tracker)."""
    text, newline, bom, encoding = notes.read_text(note_path)
    data, _raw, body = notes.split_frontmatter(text)
    data = dict(data) if isinstance(data, dict) else {}
    if stage:
        data[STAGE_MANUAL_KEY] = stage
        data["stage"] = stage
        if source:
            data[STAGE_MANUAL_KEY + "_source"] = source
        else:
            data.pop(STAGE_MANUAL_KEY + "_source", None)
    else:
        data.pop(STAGE_MANUAL_KEY, None)
        data.pop(STAGE_MANUAL_KEY + "_source", None)
    notes.write_text(note_path, notes.dump_frontmatter(data) + body, newline, bom, encoding)


def id_prefix_for(project_no: str, key: str) -> str:
    """The task-id prefix a project gets by default (see tracker.task_prefix)."""
    if project_no:
        return "p" + project_no
    slug = re.sub(r"[^a-z0-9]", "", str(key or "").lower())
    return "p" + (slug or "project")


def so_folder_name(name: str, sos: list[str], layout: str = matcher.LAYOUT_FLAT) -> str:
    """Folder name once a QTE project gets its SO(s): "QTE-054686" -> "SO-285770 (QTE-054686)".
    Flat layout: "Customer QTE-054686 0207777" -> "Customer SO-285770 (QTE-054686) 0207777".
    "" when no rename is needed (no SO, the name already shows an SO, or no QTE to wrap in the flat layout)."""
    if not sos or matcher.extract_keys(name)["so"]:
        return ""
    label = ", ".join(sos)
    if layout == matcher.LAYOUT_CUSTOMER:
        return matcher.sanitize_name("%s (%s)" % (label, name))
    found = list(matcher.QTE_RE.finditer(name))
    if not found:
        return ""
    start, end = found[0].start(), found[-1].end()
    return matcher.sanitize_name("%s%s (%s)%s" % (name[:start], label, name[start:end], name[end:]))


def add_so(note_path: str | Path, sos: list[str], id_prefix: str = "") -> list[str]:
    """Add SO numbers to the note's so: list (QTE numbers stay, so old emails still match). id_prefix, when given
    and none is stored yet, pins the task-id prefix so existing task ids survive the key change. Returns the new list."""
    text, newline, bom, encoding = notes.read_text(note_path)
    data, _raw, body = notes.split_frontmatter(text)
    data = dict(data) if isinstance(data, dict) else {}
    current = {key for value in data.get("so") or [] for key in matcher.extract_keys(str(value))["so"]}
    merged = sorted(current | set(sos))
    data["so"] = merged
    if id_prefix and not str(data.get("id_prefix") or "").strip():
        data["id_prefix"] = id_prefix
    dismissed = [value for value in data.get("so_dismissed") or [] if str(value) not in sos]
    if dismissed:
        data["so_dismissed"] = dismissed
    else:
        data.pop("so_dismissed", None)
    notes.write_text(note_path, notes.dump_frontmatter(data) + body, newline, bom, encoding)
    return merged


def dismiss_so(note_path: str | Path, so: str) -> None:
    """Stop suggesting this SO for the project."""
    text, newline, bom, encoding = notes.read_text(note_path)
    data, _raw, body = notes.split_frontmatter(text)
    data = dict(data) if isinstance(data, dict) else {}
    dismissed = [str(value) for value in data.get("so_dismissed") or []]
    if so not in dismissed:
        dismissed.append(so)
    data["so_dismissed"] = sorted(dismissed)
    notes.write_text(note_path, notes.dump_frontmatter(data) + body, newline, bom, encoding)


def _link_values(pending: dict) -> dict:
    parsed = pending.get("parsed") or {}
    return {
        "customer": parsed.get("customer", ""),
        "project_no": parsed.get("project_no", ""),
        "so": list(parsed.get("so") or []),
        "qte": list(parsed.get("qte") or []),
        "level": parsed.get("level", ""),
        "sharepoint": pending.get("sharepoint", ""),
        "team": list(pending.get("team") or []),
        "assigned": _received_date(pending).isoformat(),
    }


def link_pending(cfg: dict, pending: dict, folder_path: str | Path) -> dict:
    vault = Path(cfg.get("vault") or "")
    raw = str(folder_path or "").strip()
    if not raw or ".." in Path(raw).parts:
        raise ValueError("invalid folder: %r" % raw)
    folder = Path(raw)
    if not folder.is_absolute():
        folder = vault / raw
    folder = Path(os.path.abspath(str(folder)))
    if not _inside(vault, folder):
        raise ValueError("folder resolves outside the vault: %s" % folder)
    if not notes.lp(folder).is_dir():
        raise ValueError("folder does not exist: %s" % folder)

    updates = _link_values(pending)
    note_path = matcher.select_note(folder)
    if note_path:
        text, newline, bom, encoding = notes.read_text(note_path)
        data, _raw, body = notes.split_frontmatter(text)
        filtered = {key: value for key, value in updates.items() if notes.is_empty(data.get(key))}
        if filtered:
            merged = notes.merge_frontmatter(data, filtered)
            notes.write_text(note_path, notes.dump_frontmatter(merged) + body, newline, bom, encoding)
        return {"folder": str(folder), "note_path": str(note_path), "created": False}

    template_path = Path(cfg.get("templates_dir") or (ROOT / "templates")) / "Project Template.md"
    if not template_path.is_file():
        raise ValueError("template missing: %s" % template_path)
    name = folder.name
    note_path = str(folder / (name + ".md"))
    when = _received_date(pending)
    template = template_path.read_text(encoding="utf-8")
    values = _template_values(pending, when, "Pre-Kickoff")
    notes.lp(note_path).write_bytes(
        render_note(template, values, list(pending.get("checklist") or [])).encode("utf-8")
    )

    rules_path = Path(cfg.get("rules_path") or (HERE / "rules.yaml"))
    rules = rules_mod.load_rules(rules_path, cfg)
    rules = rules_mod.apply_checklist(rules, cfg)
    holidays = rules_mod.parse_holidays(cfg.get("holidays") or [])
    from .tracker import build_tasks

    project = Project(folder=str(folder), name=name, note_path=note_path)
    tasks, _stage = build_tasks(project, {"assignment": when}, rules, holidays, date.today())
    notes.apply_update(note_path, {}, tasks, date.today(), stages=rules_mod.workflow_stages(rules))
    return {"folder": str(folder), "note_path": note_path, "created": True}
