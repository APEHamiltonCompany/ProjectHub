from __future__ import annotations

import difflib
import hashlib
import os
import re
from datetime import date
from pathlib import Path
from typing import Iterable, Optional

import yaml

from .models import Task

BLOCK_START = "<!-- tracker:start (auto-managed; tick boxes freely) -->"
BLOCK_END = "<!-- tracker:end -->"

DUE_EMOJI = "\U0001F4C5"
DONE_EMOJI = "\u2705"
ID_EMOJI = "\U0001F194"

ID_RE = re.compile(ID_EMOJI + r"\s*([A-Za-z0-9_-]+)")
DUE_RE = re.compile(DUE_EMOJI + r"\s*(\d{4}-\d{2}-\d{2})")
DONE_RE = re.compile(DONE_EMOJI + r"\s*(\d{4}-\d{2}-\d{2})")
TIME_RE = re.compile(r"\[time::\s*[^\]]*\]")
MARK_RE = re.compile(r"^\s*[-*]\s*\[(.)\]")
CHECK_RE = re.compile(r"^\s*[-*]\s*\[([ xX-])\]\s*(.*)$")
TODO_HEADER_RE = re.compile(r"^\s*\*\*TO DO:\*\*", re.I)

FRONTMATTER_KEYS = [
    "project_no",
    "so",
    "qte",
    "customer",
    "level",
    "stage",
    "assigned",
    "tracker_updated",
    "sharepoint",
    "cpr",
    "rmt",
    "ship_date",
    "design_need_by",
    "team",
]

FILL_ONLY_KEYS = ("sharepoint", "cpr", "rmt", "ship_date", "design_need_by", "team")

STAGE_HEADINGS = ("Pre-Fabrication", "System Test", "Closeout", "Completion")
PM_HEADING = "PM schedule"
HEADING_RE = re.compile(
    r"^\*\*(?:" + "|".join(re.escape(name) for name in STAGE_HEADINGS + (PM_HEADING,)) + r")\*\*\s*$"
)


def is_empty(value) -> bool:
    return value is None or value == "" or value == [] or value == {}


def lp(path: str | Path) -> Path:
    text = str(path)
    if os.name != "nt" or text.startswith("\\\\?\\"):
        return Path(text)
    text = os.path.abspath(text)
    if text.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + text[2:])
    return Path("\\\\?\\" + text)


def read_text(path: str | Path) -> tuple[str, str, bool, str]:
    raw = lp(path).read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    if bom:
        raw = raw[3:]
    newline = "\r\n" if b"\r\n" in raw else "\n"
    try:
        text = raw.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
        encoding = "cp1252"
    return text.replace("\r\n", "\n"), newline, bom, encoding


def write_text(path: str | Path, text: str, newline: str, bom: bool, encoding: str) -> None:
    data = text.replace("\n", newline).encode(encoding, errors="replace")
    if bom:
        data = b"\xef\xbb\xbf" + data
    lp(path).write_bytes(data)


def split_frontmatter(text: str) -> tuple[dict, Optional[str], str]:
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                raw = "\n".join(lines[1:i])
                try:
                    data = yaml.safe_load(raw) or {}
                except yaml.YAMLError:
                    data = {}
                if not isinstance(data, dict):
                    data = {}
                return data, raw, "\n".join(lines[i + 1:])
    return {}, None, text


def dump_frontmatter(data: dict) -> str:
    body = yaml.safe_dump(
        data,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=1000,
    ).strip("\n")
    lines = []
    for line in body.split("\n"):
        if line.startswith("project_no:"):
            value = line.split(":", 1)[1].strip().strip("'\"")
            line = "project_no: '%s'" % value.replace("'", "''")
        lines.append(line)
    return "---\n" + "\n".join(lines) + "\n---\n"


def merge_frontmatter(data: dict, updates: dict) -> dict:
    merged = dict(data)
    for key, value in updates.items():
        if value is None:
            continue
        if key in FILL_ONLY_KEYS and not is_empty(merged.get(key)):
            continue
        if isinstance(value, (list, tuple)):
            existing = merged.get(key)
            if not isinstance(existing, list):
                existing = [] if existing in (None, "") else [existing]
            out = list(existing)
            for item in value:
                if item not in out:
                    out.append(item)
            merged[key] = out
        elif key in ("so", "qte"):
            merged[key] = [value] if value else []
        else:
            merged[key] = value
    ordered = {}
    for key in FRONTMATTER_KEYS:
        if key in merged:
            ordered[key] = merged[key]
    for key, value in merged.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


def find_block(body: str) -> tuple[list[str], Optional[int], Optional[int]]:
    lines = body.split("\n")
    start = None
    end = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == BLOCK_START and start is None:
            start = i
        elif stripped == BLOCK_END and start is not None:
            end = i
            break
    return lines, start, end


def parse_block(body: str) -> tuple[dict, list[str]]:
    lines, start, end = find_block(body)
    entries: dict[str, dict] = {}
    user_lines: list[str] = []
    if start is None or end is None:
        return entries, user_lines
    for line in lines[start + 1:end]:
        if HEADING_RE.match(line.strip()):
            continue
        match = ID_RE.search(line)
        if not match:
            if line.strip():
                user_lines.append(line)
            continue
        due = DUE_RE.search(line)
        done = DONE_RE.search(line)
        mark = MARK_RE.match(line)
        entries[match.group(1)] = {
            "mark": mark.group(1).lower() if mark else " ",
            "done_date": date.fromisoformat(done.group(1)) if done else None,
            "due": date.fromisoformat(due.group(1)) if due else None,
            "line": line,
        }
    return entries, user_lines


def format_task_line(task: Task, entry: Optional[dict], today: date) -> str:
    if entry and entry["mark"] != " ":
        return entry["line"]
    title = task.title + (" (%s)" % task.note if task.note else "")
    parts = ["-", "[ ]", title]
    token = TIME_RE.search(entry["line"]) if entry else None
    if token:
        parts.append(token.group(0))
    if task.due:
        parts.append(DUE_EMOJI + " " + task.due.isoformat())
    parts.append(ID_EMOJI + " " + task.id)
    return " ".join(parts)


def render_block(
    tasks: Iterable[Task],
    entries: dict,
    user_lines: list[str],
    today: date,
    stages: Iterable[str] = STAGE_HEADINGS,
) -> list[str]:
    lines = [BLOCK_START]
    ordered: list[str] = []
    for stage in list(stages) + [PM_HEADING]:
        if stage not in ordered:
            ordered.append(stage)
    groups: dict[str, list[Task]] = {}
    for task in tasks:
        groups.setdefault(task.stage or "", []).append(task)
    seen = set()
    for stage in ordered:
        group = groups.pop(stage, [])
        if not group:
            continue
        lines.append("**%s**" % stage)
        for task in group:
            seen.add(task.id)
            lines.append(format_task_line(task, entries.get(task.id), today))
    for stage, group in groups.items():
        if stage:
            lines.append("**%s**" % stage)
        for task in group:
            seen.add(task.id)
            lines.append(format_task_line(task, entries.get(task.id), today))
    for task_id, entry in entries.items():
        if task_id in seen or entry["mark"] == " ":
            continue
        lines.append(entry["line"])
    lines.extend(user_lines)
    lines.append(BLOCK_END)
    return lines


def insert_block(body: str, block: list[str]) -> str:
    lines, start, end = find_block(body)
    if start is not None and end is not None:
        lines = lines[:start] + block + lines[end + 1:]
        return "\n".join(lines)
    todo_index = None
    for i, line in enumerate(lines):
        if TODO_HEADER_RE.match(line):
            todo_index = i
            break
    if todo_index is not None:
        lines = lines[:todo_index + 1] + [""] + block + [""] + lines[todo_index + 1:]
        return "\n".join(lines)
    if lines and lines[-1] == "":
        lines = lines[:-1] + [""] + block + [""]
    else:
        lines = lines + [""] + block + [""]
    return "\n".join(lines)


def collect_note_tasks(body: str) -> list[Task]:
    tasks = []
    for line in body.split("\n"):
        match = CHECK_RE.match(line)
        if not match:
            continue
        due = DUE_RE.search(line)
        if not due:
            continue
        task_id = ID_RE.search(line)
        title = DUE_RE.split(match.group(2))[0].strip()
        title = re.sub(r"\s*" + ID_EMOJI + r"\s*[A-Za-z0-9_-]+", "", title).strip()
        if not task_id:
            digest = hashlib.sha1(line.strip().encode("utf-8")).hexdigest()[:8]
            ident = "note-" + digest
        else:
            ident = task_id.group(1)
        done = DONE_RE.search(line)
        tasks.append(
            Task(
                id=ident,
                title=title or "(note task)",
                due=date.fromisoformat(due.group(1)),
                done=match.group(1).lower() in ("x", "-"),
                mark=match.group(1).lower(),
                done_date=date.fromisoformat(done.group(1)) if done else None,
                source="note",
            )
        )
    return tasks


def build_text(
    text: str,
    fm_updates: dict,
    tasks: list[Task],
    today: date,
    stages: Iterable[str] = STAGE_HEADINGS,
) -> str:
    data, raw, body = split_frontmatter(text)
    new_data = merge_frontmatter(data, fm_updates) if fm_updates else data
    entries, user_lines = parse_block(body)
    block = render_block(tasks, entries, user_lines, today, stages)
    new_body = insert_block(body, block)
    if raw is None:
        header = dump_frontmatter(new_data) if (fm_updates and new_data) else ""
    elif new_data != data:
        header = dump_frontmatter(new_data)
    else:
        header = text[: len(text) - len(body)]
    return header + new_body


def diff_text(old: str, new: str, label: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=label + " (current)",
            tofile=label + " (proposed)",
        )
    )


def apply_update(
    path: str | Path,
    fm_updates: dict,
    tasks: list[Task],
    today: date,
    dry_run: bool = False,
    stages: Iterable[str] = STAGE_HEADINGS,
) -> tuple[bool, str]:
    text, newline, bom, encoding = read_text(path)
    new_text = build_text(text, fm_updates, tasks, today, stages)
    if new_text == text:
        return False, ""
    diff = diff_text(text, new_text, str(path))
    if not dry_run:
        write_text(path, new_text, newline, bom, encoding)
    return True, diff
