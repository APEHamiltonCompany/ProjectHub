from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tracker import matcher, notes
from tracker.projects import render_note
from tracker.tracker import load_config, out, yaml_list

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

CHECKBOX_EMPTY_RE = re.compile(r"^(\s*)\[\]\s?")
CHECKBOX_DONE_RE = re.compile(r"^(\s*)\[x\]\s?", re.I)
DATE_LINE_RE = re.compile(r"^\s*\*\*(\d{2}[A-Za-z]{3}\d{4})\*\*")
ASSIGNMENT_LINE_RE = re.compile(r"^\s*\*\*Project Assignment\s*-\s*(.+)$", re.I)


def normalize(lines: list[str]) -> list[str]:
    return [line.strip() for line in lines if line.strip()]


def find_duplicate_split(lines: list[str]) -> int | None:
    for i in range(1, len(lines)):
        first = normalize(lines[:i])
        second = normalize(lines[i:])
        if first and first == second:
            return i
    return None


def convert_checkboxes(lines: list[str]) -> tuple[list[str], int]:
    result = []
    count = 0
    for line in lines:
        if CHECKBOX_EMPTY_RE.match(line):
            result.append(CHECKBOX_EMPTY_RE.sub(r"\1- [ ] ", line, count=1))
            count += 1
        elif CHECKBOX_DONE_RE.match(line):
            result.append(CHECKBOX_DONE_RE.sub(r"\1- [x] ", line, count=1))
            count += 1
        else:
            result.append(line)
    return result, count


def assignment_from_body(body: str) -> dict:
    parsed = {
        "customer": "",
        "so": [],
        "qte": [],
        "project_no": "",
        "description": "",
        "level": "",
    }
    assigned = ""
    for line in body.split("\n"):
        if not assigned:
            match = DATE_LINE_RE.match(line)
            if match:
                try:
                    assigned = datetime.strptime(match.group(1).upper(), "%d%b%Y").date().isoformat()
                except ValueError:
                    assigned = ""
        match = ASSIGNMENT_LINE_RE.match(line)
        if match and not parsed["project_no"]:
            parsed = matcher.parse_assignment_subject(match.group(1).strip())
    parsed["assigned"] = assigned
    return parsed


def merge_values(folder_name: str, note_name: str, body_parsed: dict) -> dict:
    folder_keys = matcher.extract_keys(folder_name)
    note_keys = matcher.extract_keys(note_name)
    so = sorted(folder_keys["so"] | note_keys["so"] | set(body_parsed.get("so") or []))
    qte = sorted(folder_keys["qte"] | note_keys["qte"] | set(body_parsed.get("qte") or []))
    project_numbers = sorted(folder_keys["project_no"] | note_keys["project_no"])
    if body_parsed.get("project_no"):
        project_numbers = sorted(set(project_numbers) | {body_parsed["project_no"]})
    customer = body_parsed.get("customer") or ""
    if not customer:
        customer = re.split(r"\s{2,}|\s+SO-|\s+QTE-", folder_name)[0].strip()
    return {
        "project_no": project_numbers[0] if project_numbers else "",
        "so": so,
        "qte": qte,
        "customer": customer,
        "level": body_parsed.get("level") or "",
        "assigned": body_parsed.get("assigned") or "",
    }


def migrate_note(path: Path, dry_run: bool) -> tuple[list[str], str]:
    text, newline, bom, encoding = notes.read_text(path)
    data, _raw, body = notes.split_frontmatter(text)
    body_parsed = assignment_from_body(body)
    values = merge_values(path.parent.name, path.stem, body_parsed)
    changes: list[str] = []

    lines = body.split("\n")
    split = find_duplicate_split(lines)
    if split is not None:
        removed = len(lines) - split
        lines = lines[:split]
        while lines and not lines[-1].strip():
            lines.pop()
        lines.append("")
        changes.append("removed duplicated block (%d lines)" % removed)

    lines, converted = convert_checkboxes(lines)
    if converted:
        changes.append("converted %d checkbox lines" % converted)

    new_body = "\n".join(lines)
    updates = {key: value for key, value in values.items() if value not in (None, "", [])}
    updates["tracker_updated"] = date.today().isoformat()
    merged = notes.merge_frontmatter(data, updates)
    if merged != data:
        changes.append("frontmatter merged")

    new_text = notes.dump_frontmatter(merged) + new_body
    if new_text == text:
        return [], text
    if not dry_run:
        notes.write_text(path, new_text, newline, bom, encoding)
    return changes, new_text


def create_missing_note(folder: Path, cfg: dict, dry_run: bool) -> list[str]:
    folder_name = folder.name
    keys = matcher.extract_keys(folder_name)
    so = sorted(keys["so"])
    qte = sorted(keys["qte"])
    project_no = sorted(keys["project_no"])[0] if keys["project_no"] else ""
    customer = re.split(r"\s{2,}|\s+SO-|\s+QTE-", folder_name)[0].strip()
    note_path = folder / (folder_name + ".md")
    if note_path.exists():
        return []
    if dry_run:
        return ["would create note %s" % note_path]
    template_path = Path(cfg.get("templates_dir") or (ROOT / "templates")) / "Project Template.md"
    if not template_path.is_file():
        return ["template missing, cannot create %s" % note_path]
    subject = " | ".join(p for p in [customer, ", ".join(so + qte), project_no] if p)
    values = {
        "date_ddMonYYYY": date.today().strftime("%d%b%Y"),
        "subject": subject,
        "customer": customer,
        "so_list": ", ".join(so),
        "qte_list": ", ".join(qte),
        "so_yaml": yaml_list(so),
        "qte_yaml": yaml_list(qte),
        "project_no": project_no,
        "level": "",
        "description": "",
        "stage": "Unknown",
        "assigned": date.today().isoformat(),
        "sharepoint": "",
        "cpr": "",
        "rmt": "",
        "ship_date": "",
        "design_need_by": "",
        "team": "",
        "team_yaml": "",
    }
    note_path.write_bytes(
        render_note(template_path.read_text(encoding="utf-8"), values).encode("utf-8")
    )
    return ["created note %s" % note_path]


def backup_vault(vault: Path, backup_root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = backup_root / stamp
    for path in sorted(vault.rglob("*.md")):
        if not notes.lp(path).is_file():
            continue
        relative = path.relative_to(vault)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(notes.lp(path), notes.lp(destination))
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="migrate_notes", description="One-time note migration")
    parser.add_argument("--dry-run", action="store_true", help="print diffs without writing")
    parser.add_argument("--config", default=str(HERE / "config.yaml"), help="path to config.yaml")
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    cfg = load_config(config_path)
    vault = Path(cfg.get("vault") or "")
    if not vault.is_dir():
        out("vault not found: %s" % vault)
        return 1
    backup_root = Path(cfg.get("backup_root") or (ROOT / "backups"))

    out("migrate notes%s" % (" (dry run)" if args.dry_run else ""))
    out("vault: %s" % vault)
    if args.dry_run:
        out("backup: skipped in dry run")
    else:
        target = backup_vault(vault, backup_root)
        out("backup: %s" % target)

    excluded = {name.strip().lower() for name in cfg.get("exclude_folders") or []}
    changed = 0
    for folder in sorted(vault.iterdir(), key=lambda p: p.name.lower()):
        if not folder.is_dir() or folder.name.strip().lower() in excluded:
            continue
        note_path = matcher.select_note(folder)
        if note_path is None:
            messages = create_missing_note(folder, cfg, args.dry_run)
            for message in messages:
                out("  %s: %s" % (folder.name, message))
            changed += len(messages)
            continue
        path = Path(note_path)
        if args.dry_run:
            text, _nl, _bom, _enc = notes.read_text(path)
            changes, new_text = migrate_note(path, dry_run=True)
            if changes:
                changed += 1
                out("")
                out("%s" % path)
                for change in changes:
                    out("  - %s" % change)
                out(notes.diff_text(text, new_text, str(path)).rstrip("\n"))
            continue
        changes, _new_text = migrate_note(path, dry_run=False)
        if changes:
            changed += 1
            out("%s" % path)
            for change in changes:
                out("  - %s" % change)

    out("")
    out("notes changed: %d" % changed)
    if args.dry_run:
        out("dry run: no files were written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
