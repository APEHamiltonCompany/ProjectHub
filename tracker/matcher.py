from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

from . import notes
from .models import Email, Project

SO_RE = re.compile(r"\bSO[-\s]?(\d{6})\b", re.I)
QTE_RE = re.compile(r"\bQTE[-\s]?(\d{6})\b", re.I)
PROJECT_RE = re.compile(r"\b(02\d{5})(?:-\d{3})?\b")
LEVEL_RE = re.compile(r"^L\d$", re.I)
ASSIGNMENT_PREFIX_RE = re.compile(r"^\s*(?:(?:re|fw|fwd)\s*:\s*)*", re.I)
ILLEGAL_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
ARCHIVE_DIR = "_Archive"
TASKS_DIR = "My Tasks"


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


def project_from_folder(folder: str | Path) -> Project:
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
            project.stage = str(data.get("stage") or "Unknown")
            project.assigned = str(data.get("assigned") or "")
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
        project.customer = name.split("  ")[0].split(" SO-")[0].split(" QTE-")[0].strip()
    return project


def build_projects(vault: str | Path, exclude_folders: Iterable[str] = ()) -> list[Project]:
    vault = Path(vault)
    excluded = {name.strip().lower() for name in exclude_folders or []}
    projects = []
    if not vault.is_dir():
        return projects
    for entry in sorted(vault.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_dir():
            continue
        if entry.name.startswith(".") or entry.name.strip().lower() in excluded:
            continue
        if is_reserved_dir(entry.name):
            continue
        projects.append(project_from_folder(entry))
    return projects


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


def folder_name_for(customer: str, so: list[str], qte: list[str], project_no: str) -> str:
    ident = so[0] if so else (qte[0] if qte else "")
    parts = [customer, ident, project_no]
    return sanitize_name(" ".join(p for p in parts if p))
