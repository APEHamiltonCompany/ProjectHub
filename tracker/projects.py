from __future__ import annotations

import os
import re
import shutil
from datetime import date, datetime
from pathlib import Path

from . import matcher, notes
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
) -> dict:
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
        return {"folder": str(folder), "note_path": str(note_path), "files_created": []}

    files_created: list[str] = []
    notes.lp(folder).mkdir(parents=True, exist_ok=True)
    values = _template_values(pending, when, project.stage)
    template = template_path.read_text(encoding="utf-8")
    notes.lp(note_path).write_bytes(
        render_note(template, values, list(pending.get("checklist") or [])).encode("utf-8")
    )
    files_created.append(str(note_path))

    template_xlsx = Path(cfg.get("time_estimate_template") or "")
    if str(template_xlsx) and notes.lp(template_xlsx).is_file():
        target = folder / (when.strftime("%d%b%Y").upper() + "_TimeEstimates.xlsx")
        shutil.copy2(notes.lp(template_xlsx), notes.lp(target))
        files_created.append(str(target))

    rules_path = Path(cfg.get("rules_path") or (HERE / "rules.yaml"))
    rules = rules_mod.load_rules(rules_path)
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
    pending["status"] = "created"
    return {"folder": str(folder), "note_path": str(note_path), "files_created": files_created}


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
    rules = rules_mod.load_rules(rules_path)
    holidays = rules_mod.parse_holidays(cfg.get("holidays") or [])
    from .tracker import build_tasks

    project = Project(folder=str(folder), name=name, note_path=note_path)
    tasks, _stage = build_tasks(project, {"assignment": when}, rules, holidays, date.today())
    notes.apply_update(note_path, {}, tasks, date.today(), stages=rules_mod.workflow_stages(rules))
    return {"folder": str(folder), "note_path": note_path, "created": True}
