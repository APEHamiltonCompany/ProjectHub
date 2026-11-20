from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

from . import notes
from .models import Email, Project

SO_RE = re.compile(r"(?<![A-Za-z0-9])SO[-\s]?(\d{6})(?![A-Za-z0-9])", re.I)
QTE_RE = re.compile(r"(?<![A-Za-z0-9])QTE[-\s]?(\d{6})(?![A-Za-z0-9])", re.I)
PROJECT_RE = re.compile(r"(?<![A-Za-z0-9])(02\d{5})(?:-\d{3})?(?![A-Za-z0-9])")
LEVEL_RE = re.compile(r"^L\d$", re.I)
ASSIGNMENT_PREFIX_RE = re.compile(r"^\s*(?:(?:re|fw|fwd)\s*:\s*)*", re.I)
ILLEGAL_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
ARCHIVE_DIR = "_Archive"
TASKS_DIR = "My Tasks"
LAYOUT_FLAT = "flat"
LAYOUT_CUSTOMER = "customer/so"
LAYOUTS = (LAYOUT_FLAT, LAYOUT_CUSTOMER)


def is_reserved_dir(name: str) -> bool:
    return str(name).strip().lower() in (ARCHIVE_DIR.lower(), TASKS_DIR.lower())


def extract_keys(text: str) -> dict[str, set[str]]:
    text = text or ""
    return {
        "so": {"SO-" + m.group(1) for m in SO_RE.finditer(text)},
        "qte": {"QTE-" + m.group(1) for m in QTE_RE.finditer(text)},
        "project_no": {m.group(1) for m in PROJECT_RE.finditer(text)},
    }


def has_keys(keys: dict[str, set[str]]) -> bool:
    return any(keys.values())


def strip_assignment_prefix(subject: str) -> str:
    text = ASSIGNMENT_PREFIX_RE.sub("", subject or "").strip()
    while re.match(r"^project\s+assignment\s*-", text, re.I):
        text = re.sub(r"^project\s+assignment\s*-\s*", "", text, flags=re.I).strip()
    return text


def addressed_to_me(to: str, me: dict | None) -> Optional[bool]:
    """True when the To line names me (email, "First Last" or "Last, First"); None if I am not configured."""
    name = re.sub(r"\s+", " ", str((me or {}).get("name") or "")).strip().lower()
    email = str((me or {}).get("email") or "").strip().lower()
    if not name and not email:
        return None
    line = re.sub(r"\s+", " ", str(to or "")).lower()
    if email and email in line:
        return True
    if name:
        if re.search(r"(^|[;,<\"'(]\s*)%s(\s*$|\s*[;,>\"')])" % re.escape(name), line):
            return True
        parts = name.split(" ")
        if len(parts) > 1:
            flipped = "%s, %s" % (parts[-1], " ".join(parts[:-1]))
            if flipped in line:
                return True
    return False


def parse_assignment_subject(subject: str) -> dict:
    text = strip_assignment_prefix(subject).strip().strip("*").strip()
    parts = [p.strip() for p in text.split("|")]
    if len(parts) == 1:
        parts = [p.strip() for p in re.split(r"\s{2,}|\s_\s", text) if p.strip()]
    result = {
        "customer": "",
        "so": [],
        "qte": [],
        "project_no": "",
        "description": "",
        "level": "",
    }
    if not parts:
        return result
    result["customer"] = parts[0].strip()
    if len(parts) > 1:
        keys = extract_keys(parts[1])
        result["so"] = sorted(keys["so"])
        result["qte"] = sorted(keys["qte"])
    if len(parts) > 2:
        match = PROJECT_RE.search(parts[2])
        if match:
            result["project_no"] = match.group(1)
    if len(parts) > 3:
        result["description"] = parts[3].strip()
    if len(parts) > 4 and LEVEL_RE.match(parts[4].strip()):
        result["level"] = parts[4].strip().upper()
    return result


def sanitize_name(name: str) -> str:
    cleaned = ILLEGAL_RE.sub("", name or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(".")
    return cleaned or "Project"


def select_note(folder: str | Path) -> Optional[str]:
    folder = Path(folder)
    try:
        entries = list(folder.iterdir())
    except OSError:
        return None
    md_files = [p for p in entries if p.suffix.lower() == ".md" and notes.lp(p).is_file()]
    if not md_files:
        return None
    for path in md_files:
        if path.stem == folder.name:
            return str(path)
    if len(md_files) == 1:
        return str(md_files[0])
    return str(max(md_files, key=lambda p: notes.lp(p).stat().st_mtime))


def project_from_folder(folder: str | Path, customer: str = "") -> Project:
    folder = Path(folder)
    name = folder.name
    note_path = select_note(folder)
    project = Project(folder=str(folder), name=name, note_path=note_path)
    keys = extract_keys(name)
    if note_path:
        keys["so"] |= extract_keys(Path(note_path).stem)["so"]
        keys["qte"] |= extract_keys(Path(note_path).stem)["qte"]
        keys["project_no"] |= extract_keys(Path(note_path).stem)["project_no"]
        try:
            text, _nl, _bom, _enc = notes.read_text(note_path)
            data, _raw, _body = notes.split_frontmatter(text)
        except OSError:
            data = {}
        if data:
            project.customer = str(data.get("customer") or "")
            project.level = str(data.get("level") or "")
            project.stage_manual = str(data.get("stage_manual") or "").strip()
            project.stage_manual_source = str(data.get("stage_manual_source") or "").strip() if project.stage_manual else ""
            project.stage = project.stage_manual or str(data.get("stage") or "Unknown")
            project.assigned = str(data.get("assigned") or "")
            project.id_prefix = str(data.get("id_prefix") or "").strip()
            project.so_dismissed = sorted(
                {key for value in data.get("so_dismissed") or [] for key in extract_keys(str(value))["so"]}
            )
            pn = data.get("project_no")
            if pn:
                keys["project_no"] |= set(extract_keys(str(pn))["project_no"]) or {str(pn)}
            for value in data.get("so") or []:
                keys["so"] |= extract_keys(str(value))["so"]
            for value in data.get("qte") or []:
                keys["qte"] |= extract_keys(str(value))["qte"]
    project.so = sorted(keys["so"])
    project.qte = sorted(keys["qte"])
    project.project_no = sorted(keys["project_no"])[0] if keys["project_no"] else ""
    if not project.customer:
        project.customer = customer or name.split("  ")[0].split(" SO-")[0].split(" QTE-")[0].strip()
    return project


def _subdirs(folder: Path, excluded: set[str]) -> list[Path]:
    try:
        entries = sorted(folder.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []
    return [
        entry
        for entry in entries
        if entry.is_dir()
        and not entry.name.startswith(".")
        and entry.name.strip().lower() not in excluded
        and not is_reserved_dir(entry.name)
    ]


def is_customer_dir(folder: Path) -> bool:
    """A vault child is a customer folder when its own name has no SO/QTE/project number
    but at least one of its subfolders does (the <vault>/<Customer>/SO-###### layout)."""
    if has_keys(extract_keys(folder.name)):
        return False
    return any(has_keys(extract_keys(sub.name)) for sub in _subdirs(folder, set()))


def project_dirs(
    vault: str | Path, exclude_folders: Iterable[str] = (), layout: str = LAYOUT_FLAT
) -> list[tuple[Path, str]]:
    """(project folder, customer folder name or "") for flat and customer/SO layouts.

    customer/so: a vault child whose name has an SO/QTE/project number is a (legacy flat)
    project; every other child is a customer folder. Its subfolders are projects when their
    name has an SO/QTE/project number or they hold a .md note (skips Logs, BACKUP, ...).
    flat: children are projects unless they look like customer folders (is_customer_dir)."""
    vault = Path(vault)
    excluded = {name.strip().lower() for name in exclude_folders or []}
    if not vault.is_dir():
        return []
    rows: list[tuple[Path, str]] = []
    for entry in _subdirs(vault, excluded):
        if layout == LAYOUT_CUSTOMER:
            customer = not has_keys(extract_keys(entry.name))
        else:
            customer = is_customer_dir(entry)
        if customer:
            rows.extend(
                (sub, entry.name)
                for sub in _subdirs(entry, excluded)
                if layout != LAYOUT_CUSTOMER or has_keys(extract_keys(sub.name)) or select_note(sub)
            )
        else:
            rows.append((entry, ""))
    return rows


def relative_folder(vault: str | Path, folder: str | Path) -> str:
    try:
        return Path(folder).relative_to(Path(vault)).as_posix()
    except ValueError:
        return Path(folder).name


def build_projects(
    vault: str | Path, exclude_folders: Iterable[str] = (), layout: str = LAYOUT_FLAT
) -> list[Project]:
    return [
        project_from_folder(folder, customer)
        for folder, customer in project_dirs(vault, exclude_folders, layout)
    ]


def build_index(projects: Iterable[Project]) -> dict[str, dict[str, Project]]:
    index = {"project_no": {}, "so": {}, "qte": {}}
    for project in projects:
        if project.project_no:
            index["project_no"].setdefault(project.project_no, project)
        for value in project.so:
            index["so"].setdefault(value, project)
        for value in project.qte:
            index["qte"].setdefault(value, project)
    return index


def match_email(email: Email, index: dict[str, dict[str, Project]]) -> tuple[Optional[Project], dict[str, set[str]]]:
    keys = extract_keys(email.subject)
    body_keys = extract_keys(email.body_text)
    for name in ("so", "qte", "project_no"):
        keys[name] |= body_keys[name]
    for project_no in sorted(keys["project_no"]):
        if project_no in index["project_no"]:
            return index["project_no"][project_no], keys
    for so in sorted(keys["so"]):
        if so in index["so"]:
            return index["so"][so], keys
    for qte in sorted(keys["qte"]):
        if qte in index["qte"]:
            return index["qte"][qte], keys
    return None, keys


def folder_layout(cfg: dict) -> str:
    value = str((cfg or {}).get("project_folder_layout") or LAYOUT_FLAT).strip().lower()
    return value if value in LAYOUTS else LAYOUT_FLAT


def folder_name_for(
    customer: str, so: list[str], qte: list[str], project_no: str, layout: str = LAYOUT_FLAT
) -> str:
    ident = so[0] if so else (qte[0] if qte else "")
    if layout == LAYOUT_CUSTOMER:
        leaf = sanitize_name(ident or project_no or "Project")
        return sanitize_name(customer or "Unknown Customer") + "/" + leaf
    parts = [customer, ident, project_no]
    return sanitize_name(" ".join(p for p in parts if p))


def split_folder(raw: str) -> list[str]:
    """Split a vault-relative project folder ("Name" or "Customer/Name"); [] if invalid."""
    parts = [p.strip() for p in re.split(r"[\\/]", str(raw or ""))]
    if not 1 <= len(parts) <= 2 or any(p in ("", ".", "..") for p in parts):
        return []
    return [sanitize_name(p) for p in parts]
