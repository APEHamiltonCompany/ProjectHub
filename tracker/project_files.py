"""Template files copied into project folders (TimeEstimates, parts request, test matrix, ...).

Config (see config.example.yaml):

    templates_root: R:/APE_AE/TEMPLATES
    project_files:
      files:
        - {id: time_estimate, label: TimeEstimates, source: "Time Estimate/TimeEstimates*.xlsx",
           name: "TimeEstimates {ident}"}
        - {id: test_matrix_l1, group: test_matrix, label: L1Carrier, source: ..., name: ...}
      groups:
        test_matrix: {label: Test matrix, none_allowed: true}

Files without a group are always copied when a project is created. For each group a person
picks one file (or "None" when none_allowed); the pick is stored in the project note's
frontmatter under the group id, as the file's label.
"""
from __future__ import annotations

import fnmatch
import os
import re
import shutil
import secrets
from pathlib import Path

from . import matcher, notes

NONE = "none"
NONE_LABEL = "None"
TOKEN_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
VERSION_RE = re.compile(r"[vV](\d+(?:\.\d+)*)")
DUPLICATE_RE = re.compile(r"\s\(\d+\)$")
TOKEN_NAMES = {"ident": "SO/QTE number", "so": "SO number", "qte": "QTE number", "project_no": "project number"}


def templates_root(cfg: dict) -> Path:
    return Path(os.path.expandvars(str(cfg.get("templates_root") or "")))


def specs(cfg: dict) -> tuple[list[dict], dict[str, dict]]:
    """(file items, groups). Without project_files, falls back to time_estimate_template."""
    raw = cfg.get("project_files")
    if not isinstance(raw, dict):
        legacy = str(cfg.get("time_estimate_template") or "").strip()
        if not legacy:
            return [], {}
        item = {"id": "time_estimate", "label": "TimeEstimates", "source": legacy, "name": "TimeEstimates {ident}", "group": ""}
        return [item], {}
    items = []
    seen = set()
    for entry in raw.get("files") or []:
        if not isinstance(entry, dict):
            continue
        item_id = str(entry.get("id") or "").strip()
        if not item_id or item_id in seen or not str(entry.get("source") or "").strip():
            continue
        seen.add(item_id)
        items.append(
            {
                "id": item_id,
                "label": str(entry.get("label") or item_id),
                "source": str(entry.get("source")).strip(),
                "name": str(entry.get("name") or "").strip(),
                "group": str(entry.get("group") or "").strip(),
            }
        )
    groups: dict[str, dict] = {}
    for group_id, entry in (raw.get("groups") or {}).items():
        entry = entry if isinstance(entry, dict) else {}
        options = [item for item in items if item["group"] == str(group_id)]
        if options:
            groups[str(group_id)] = {
                "id": str(group_id),
                "label": str(entry.get("label") or group_id),
                "none_allowed": bool(entry.get("none_allowed", False)),
                "options": options,
            }
    for item in items:
        if item["group"] and item["group"] not in groups:
            groups[item["group"]] = {
                "id": item["group"],
                "label": item["group"].replace("_", " ").capitalize(),
                "none_allowed": False,
                "options": [option for option in items if option["group"] == item["group"]],
            }
    return items, groups


def item_by_id(cfg: dict, item_id: str) -> dict | None:
    items, _groups = specs(cfg)
    return next((item for item in items if item["id"] == str(item_id or "").strip()), None)


def group_by_id(cfg: dict, group_id: str) -> dict | None:
    return specs(cfg)[1].get(str(group_id or "").strip())


def tokens_for(customer: str = "", so=None, qte=None, project_no: str = "") -> dict:
    so = [str(item) for item in so or [] if item]
    qte = [str(item) for item in qte or [] if item]
    project_no = str(project_no or "")
    return {
        "ident": so[0] if so else qte[0] if qte else project_no,
        "so": so[0] if so else "",
        "qte": qte[0] if qte else "",
        "project_no": project_no,
        "customer": str(customer or ""),
    }


def _source_pattern(cfg: dict, item: dict) -> Path:
    path = Path(os.path.expandvars(item["source"]))
    return path if path.is_absolute() else templates_root(cfg) / path


def _suffix(item: dict) -> str:
    suffix = Path(item["source"]).suffix
    return "" if any(ch in suffix for ch in "*?[") else suffix


def dest_name(item: dict, tokens: dict) -> str:
    """File name of the copy; raises ValueError when the project lacks a needed number."""

    def replace(match):
        value = str(tokens.get(match.group(1)) or "").strip()
        if not value:
            raise ValueError("this project has no %s" % TOKEN_NAMES.get(match.group(1), match.group(1)))
        return value

    pattern = item["name"] or Path(item["source"]).stem
    name = matcher.sanitize_name(TOKEN_RE.sub(replace, pattern)).strip(" .")
    if not name:
        raise ValueError("empty file name for %s" % item["label"])
    suffix = _suffix(item)
    if suffix and not name.lower().endswith(suffix.lower()):
        name += suffix
    return name


def _version(stem: str) -> tuple[int, ...]:
    found = VERSION_RE.findall(stem)
    return tuple(int(part) for part in found[-1].split(".")) if found else ()


def find_source(cfg: dict, item: dict) -> Path:
    """Newest template matching the item's source pattern; skips Office lock files and "(1)" copies."""
    pattern = _source_pattern(cfg, item)
    folder = pattern.parent
    try:
        entries = list(notes.lp(folder).iterdir())
    except OSError:
        raise FileNotFoundError("template folder not found: %s" % folder)
    candidates = []
    for entry in entries:
        name = entry.name
        if name.startswith("~$") or not fnmatch.fnmatch(name.lower(), pattern.name.lower()):
            continue
        if DUPLICATE_RE.search(Path(name).stem) or not notes.lp(folder / name).is_file():
            continue
        candidates.append(folder / name)
    if not candidates:
        raise FileNotFoundError("no template matching %s" % pattern)
    return max(candidates, key=lambda p: (_version(p.stem), notes.lp(p).stat().st_mtime))


def find_existing(folder: Path, name: str) -> Path | None:
    target = Path(folder) / name
    if notes.lp(target).is_file():
        return target
    try:
        for entry in notes.lp(folder).iterdir():
            if entry.name.lower() == name.lower() and entry.is_file():
                return Path(folder) / entry.name
    except OSError:
        pass
    return None


def ensure_file(cfg: dict, item: dict, folder: str | Path, tokens: dict) -> tuple[Path, bool]:
    """Path of the item's copy in folder, copying it from the template if missing. Never overwrites."""
    folder = Path(folder)
    if not notes.lp(folder).is_dir():
        raise FileNotFoundError("project folder not found: %s" % folder)
    name = dest_name(item, tokens)
    existing = find_existing(folder, name)
    if existing is not None:
        return existing, False
    source = find_source(cfg, item)
    target = folder / name
    tmp = folder / ("%s.%s.tmp" % (name, secrets.token_hex(4)))
    try:
        shutil.copy2(notes.lp(source), notes.lp(tmp))
        try:
            os.link(notes.lp(tmp), notes.lp(target))
        except FileExistsError:
            return target, False
        except OSError:
            if notes.lp(target).exists():
                return target, False
            os.replace(notes.lp(tmp), notes.lp(target))
    finally:
        if notes.lp(tmp).exists():
            os.remove(notes.lp(tmp))
    return target, True


def validate_choices(cfg: dict, choices: dict | None) -> tuple[dict[str, str], list[str]]:
    """Normalise {group: item id | label | "none"}; every group must be answered."""
    _items, groups = specs(cfg)
    choices = choices if isinstance(choices, dict) else {}
    picked: dict[str, str] = {}
    errors: list[str] = []
    for group_id, group in groups.items():
        value = resolve_option(group, choices.get(group_id))
        if value is None:
            errors.append("Choose a %s" % group["label"].lower())
        elif value == NONE and not group["none_allowed"]:
            errors.append("A %s is required" % group["label"].lower())
        else:
            picked[group_id] = value
    return picked, errors


def resolve_option(group: dict, value) -> str | None:
    """item id for a group option given an id or label, NONE, or None if unset/unknown."""
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text == NONE:
        return NONE
    for option in group["options"]:
        if text in (option["id"].lower(), option["label"].lower()):
            return option["id"]
    return None


def selected_items(cfg: dict, choices: dict[str, str] | None) -> list[dict]:
    items, groups = specs(cfg)
    chosen = set((choices or {}).values())
    return [item for item in items if not item["group"] or item["id"] in chosen]


def copy_files(cfg: dict, folder: str | Path, tokens: dict, items: list[dict]) -> tuple[list[str], list[str]]:
    created, skipped = [], []
    for item in items:
        try:
            path, made = ensure_file(cfg, item, folder, tokens)
        except (OSError, ValueError) as exc:
            skipped.append("%s: %s" % (item["label"], exc))
            continue
        if made:
            created.append(str(path))
    return created, skipped


def read_choices(cfg: dict, frontmatter: dict | None) -> dict[str, str]:
    """{group: item id | NONE} from a note's frontmatter (unset groups are left out)."""
    data = frontmatter if isinstance(frontmatter, dict) else {}
    picked = {}
    for group_id, group in specs(cfg)[1].items():
        value = resolve_option(group, data.get(group_id))
        if value is not None:
            picked[group_id] = value
    return picked


def frontmatter_values(cfg: dict, choices: dict[str, str]) -> dict[str, str]:
    values = {}
    for group_id, value in choices.items():
        item = item_by_id(cfg, value)
        values[group_id] = NONE_LABEL if value == NONE else (item["label"] if item else value)
    return values


def pick_message(cfg: dict) -> str:
    groups = specs(cfg)[1]
    return "Choose %s on the project page (Project files)" % " and ".join(
        group["label"].lower() for group in groups.values()
    )


def status(cfg: dict, folder: str | Path, tokens: dict, frontmatter: dict | None) -> list[dict]:
    """Rows for the project page: always-copied files, then one row per group."""
    items, groups = specs(cfg)
    choices = read_choices(cfg, frontmatter)
    rows = []

    def file_row(item: dict) -> dict:
        try:
            name = dest_name(item, tokens)
        except ValueError as exc:
            return {"item": item["id"], "file": "", "exists": False, "error": str(exc)}
        existing = find_existing(Path(folder), name) if str(folder or "") else None
        return {"item": item["id"], "file": existing.name if existing else name, "exists": existing is not None, "error": ""}

    for item in items:
        if not item["group"]:
            rows.append(dict(file_row(item), label=item["label"], group=None))
    for group_id, group in groups.items():
        value = choices.get(group_id, "")
        row = {"label": group["label"], "group": group, "choice": value, "item": "", "file": "", "exists": False, "error": ""}
        item = item_by_id(cfg, value) if value and value != NONE else None
        if item:
            row.update(file_row(item))
        rows.append(row)
    return rows


def groups_for_ui(cfg: dict) -> list[dict]:
    return [
        {
            "id": group["id"],
            "label": group["label"],
            "none_allowed": group["none_allowed"],
            "options": [{"id": item["id"], "label": item["label"]} for item in group["options"]],
        }
        for group in specs(cfg)[1].values()
    ]
