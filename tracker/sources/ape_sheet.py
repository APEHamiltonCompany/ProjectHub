from __future__ import annotations

import fnmatch
import os
import re
import shutil
import tempfile
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook

from .. import matcher

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
COPY_PREFIX = "APE_Tracking_"
PLACEHOLDERS = {"", "na", "n/a", "-", "none", "#lab id#"}
INITIALS_RE = re.compile(r"^[A-Z]{2,3}$")
TOKEN_RE = re.compile(r"[^A-Za-z]+")
STATUS_NUMBER_RE = re.compile(r"^\s*(\d{1,2})")
LEVEL_RE = re.compile(r"^\s*(L[123])\b", re.I)
HUB_STAGE_GROUPS = {
    "pre-kickoff": 1,
    "design": 2,
    "design review": 2,
    "apps plan": 2,
    "pre-fabrication": 2,
    "fabrication/test": 3,
    "system test": 3,
    "closeout": 4,
    "completion": 4,
}
FIELD_MATCHERS = (
    ("customer", lambda h: h == "customer"),
    ("so", lambda h: h.startswith("sales order") or h.startswith("so#")),
    ("sk", lambda h: h.startswith("sk#")),
    ("project_id", lambda h: h == "project id"),
    ("instrument", lambda h: h.startswith("instrument")),
    ("description", lambda h: h.startswith("description")),
    ("level", lambda h: h.startswith("level")),
    ("pm", lambda h: h == "pm"),
    ("me", lambda h: h == "me" or h.startswith("ape mech")),
    ("support", lambda h: h.startswith("suppt staff") or h.startswith("support engr")),
    ("ae", lambda h: h == "ae" or h.startswith("automation eng")),
    ("lab", lambda h: h.startswith("ape lab build")),
    ("sales_manager", lambda h: h.startswith("sales manager")),
    ("assigned", lambda h: h == "assigned" or h.startswith("eng. assignment date")),
    ("baseline", lambda h: h.startswith("baseline delivery")),
    ("ship_date", lambda h: h.startswith("ship date")),
    ("so_status", lambda h: h == "so status"),
    ("health", lambda h: h == "project health"),
    ("comments", lambda h: h.startswith("comments / history")),
    ("need_by", lambda h: h.startswith("instr need-by")),
)
FIELD_NAMES = tuple(name for name, _match in FIELD_MATCHERS)
TABS = (("Active", "Active Projects"), ("New", "New Projects"))


def _sheet_cfg(cfg: dict) -> dict:
    return dict((cfg or {}).get("ape_sheet") or {})


def candidate_roots() -> list[Path]:
    home = Path.home()
    candidates = [home / "OneDrive - Hamilton Company"]
    try:
        for entry in home.iterdir():
            if entry.name.startswith("Hamilton Company") or entry.name.startswith("OneDrive - "):
                candidates.append(entry)
    except OSError:
        pass
    for name in ("OneDriveCommercial", "OneDrive"):
        value = os.environ.get(name)
        if value:
            candidates.append(Path(value))
    roots: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        try:
            if not path.is_dir():
                continue
            key = str(path.resolve()).lower()
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        roots.append(path)
    return roots


def _sync_name(cfg: dict) -> str:
    return str(_sheet_cfg(cfg).get("sync_filename") or "APE Project Tracking.xlsx").strip().lower()


def _walk_for(root: Path, name: str, depth: int):
    stack = [(root, 0)]
    while stack:
        folder, level = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                if entry.is_dir():
                    if level + 1 < depth:
                        stack.append((Path(entry.path), level + 1))
                    continue
            except OSError:
                continue
            if entry.name.lower() != name:
                continue
            if not Path(entry.path).parent.name.strip().lower().endswith("tracking documents"):
                continue
            yield Path(entry.path)


def _find_synced(roots: list[Path], name: str) -> Path | None:
    best: tuple[float, Path] | None = None
    for root in roots:
        for found in _walk_for(root, name, 4):
            try:
                mtime = found.stat().st_mtime
            except OSError:
                continue
            if best is None or mtime > best[0]:
                best = (mtime, found)
    return best[1] if best else None


def find_synced(cfg: dict) -> Path | None:
    return _find_synced(candidate_roots(), _sync_name(cfg))


def _in_downloads(path: Path) -> bool:
    return any(part.lower() == "downloads" for part in path.parts)


def _under_roots(path: Path, roots: list[Path]) -> bool:
    try:
        target = path.resolve()
    except OSError:
        target = path
    for root in roots:
        try:
            target.relative_to(root.resolve())
            return True
        except (OSError, ValueError):
            continue
    return False


def _search_folder(folder: Path, pattern: str) -> Path | None:
    best: tuple[float, Path] | None = None
    try:
        entries = list(folder.iterdir())
    except OSError:
        return None
    for entry in entries:
        if entry.name.startswith("~$"):
            continue
        if not fnmatch.fnmatch(entry.name.lower(), pattern):
            continue
        try:
            if not entry.is_file():
                continue
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if best is None or mtime > best[0]:
            best = (mtime, entry)
    return best[1] if best else None


def _resolve_path(raw: str, pattern: str) -> Path | None:
    if not raw:
        return None
    path = Path(raw)
    if path.is_file():
        return path
    if not path.is_dir():
        return None
    return _search_folder(path, pattern)


def resolve_info(cfg: dict) -> dict:
    sheet = _sheet_cfg(cfg)
    pattern = str(sheet.get("pattern") or "APE Project Tracking*.xlsx").lower()
    roots = candidate_roots()
    override = str((cfg or {}).get("ape_sheet_path") or "").strip()
    if override:
        path = _resolve_path(override, pattern)
        if path is not None and path.suffix.lower() == ".xlsx":
            return {"path": path, "kind": "downloads" if _in_downloads(path) else "override"}
    if sheet.get("auto_find", True):
        found = _find_synced(roots, _sync_name(cfg))
        if found is not None:
            return {"path": found, "kind": "synced"}
    path = _resolve_path(str(sheet.get("path") or "").strip(), pattern)
    if path is None:
        return {"path": None, "kind": ""}
    if _in_downloads(path):
        return {"path": path, "kind": "downloads"}
    return {"path": path, "kind": "synced" if _under_roots(path, roots) else "file"}


def resolve_source(cfg: dict) -> Path | None:
    return resolve_info(cfg)["path"]


def shortcut_warning(cfg: dict) -> str:
    if find_synced(cfg) is not None:
        return ""
    for root in candidate_roots():
        try:
            entries = list(os.scandir(root))
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if not fnmatch.fnmatch(entry.name.lower(), "ape project tracking*.url"):
                continue
            try:
                if not entry.is_file():
                    continue
            except OSError:
                continue
            return (
                "Found a OneDrive web shortcut (.url), not the file. In SharePoint open "
                "General \u203a Tracking Documents and click Sync (or 'Add shortcut to My files' "
                "on the folder) so the real file syncs."
            )
    return ""


def _prune(copy_dir: Path, keep: int) -> None:
    try:
        files = [
            entry
            for entry in copy_dir.iterdir()
            if entry.is_file() and entry.name.startswith(COPY_PREFIX) and entry.suffix.lower() == ".xlsx"
        ]
    except OSError:
        return
    files.sort(key=lambda entry: entry.name, reverse=True)
    for old in files[max(1, int(keep)):]:
        try:
            old.unlink()
        except OSError:
            pass


def snapshot_copy(source: Path, copy_dir: Path, keep: int = 5) -> Path:
    source = Path(source)
    copy_dir = Path(copy_dir)
    copy_dir.mkdir(parents=True, exist_ok=True)
    stat = source.stat()
    stamp = datetime.fromtimestamp(stat.st_mtime).strftime("%Y%m%d-%H%M%S")
    target = copy_dir / ("%s%s.xlsx" % (COPY_PREFIX, stamp))
    if target.is_file() and target.stat().st_size == stat.st_size:
        _prune(copy_dir, keep)
        return target
    handle, temp = tempfile.mkstemp(dir=str(copy_dir), prefix=".ape-", suffix=".xlsx")
    os.close(handle)
    try:
        shutil.copy2(source, temp)
        os.replace(temp, target)
    except Exception:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise
    _prune(copy_dir, keep)
    return target


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    text = str(value).strip()
    if text.lower() in PLACEHOLDERS:
        return ""
    return text


def _header(value) -> str:
    text = str(value or "").replace("\n", " ").replace("\r", " ")
    return re.sub(r"\s+", " ", text).strip().lower()


def _columns(row) -> dict[str, int]:
    columns: dict[str, int] = {}
    for index, cell in enumerate(row or ()):
        header = _header(cell)
        if not header:
            continue
        for name, matches in FIELD_MATCHERS:
            if name in columns:
                continue
            if matches(header):
                columns[name] = index
    return columns


def _looks_like_name(value: str) -> bool:
    if " " not in value:
        return False
    if not re.search(r"[A-Za-z]", value):
        return False
    if value[:1].isdigit():
        return False
    if value.lower().startswith("level-"):
        return False
    return True


def _names_map(wb) -> dict[str, str]:
    names: dict[str, str] = {}
    if "Dropdowns" not in wb.sheetnames:
        return names
    for row in wb["Dropdowns"].iter_rows(values_only=True):
        cells = list(row or ())
        for index in range(len(cells) - 1):
            first, second = cells[index], cells[index + 1]
            if not isinstance(first, str) or not isinstance(second, str):
                continue
            key = first.strip()
            value = second.strip()
            if not INITIALS_RE.match(key) or key in names:
                continue
            if not _looks_like_name(value):
                continue
            names[key] = value
    return names


def _resolve_names(value: str, names: dict[str, str]) -> str:
    if not value:
        return ""
    parts = [part.strip() for part in re.split(r"[/,]", value) if part.strip()]
    resolved = [names.get(part.upper()) or part for part in parts]
    return ", ".join(resolved)


def _record(row, columns: dict[str, int], tab: str, index: int, names: dict[str, str]) -> dict | None:
    values = {name: "" for name in FIELD_NAMES}
    for name, column in columns.items():
        values[name] = _text(row[column] if column < len(row or ()) else None)
    keys = matcher.extract_keys((values["so"] + " " + values["sk"]).strip())
    key_map = {name: sorted(items) for name, items in keys.items()}
    if not values["customer"] and not any(key_map.values()):
        return None
    record = {"tab": tab, "row": index}
    record.update(values)
    record["comments"] = record["comments"][:1500]
    record["pm_name"] = _resolve_names(values["pm"], names)
    record["me_name"] = _resolve_names(values["me"], names)
    record["support_name"] = _resolve_names(values["support"], names)
    record["ae_name"] = _resolve_names(values["ae"], names)
    record["keys"] = key_map
    return record


def _rows_from(ws, tab: str, names: dict[str, str]) -> list[dict]:
    rows: list[dict] = []
    columns: dict[str, int] = {}
    header_seen = False
    for index, row in enumerate(ws.iter_rows(values_only=True), start=1):
        if not header_seen:
            columns = _columns(row)
            header_seen = True
            continue
        if not columns:
            break
        record = _record(row, columns, tab, index, names)
        if record is not None:
            rows.append(record)
    return rows


def _read(path: Path) -> tuple[list[dict], dict[str, str]]:
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        names = _names_map(wb)
        rows: list[dict] = []
        for tab, sheet_name in TABS:
            if sheet_name not in wb.sheetnames:
                continue
            rows.extend(_rows_from(wb[sheet_name], tab, names))
        return rows, names
    finally:
        wb.close()


def load(cfg: dict) -> dict:
    sheet = _sheet_cfg(cfg)
    try:
        stale_hours = int(sheet.get("stale_hours") or 48)
    except (TypeError, ValueError):
        stale_hours = 48
    result = {
        "ok": False,
        "error": "",
        "source": "",
        "source_mtime": "",
        "source_mtime_label": "",
        "kind": "",
        "warning": "",
        "copy": "",
        "url": str(sheet.get("url") or ""),
        "my_initials": str(sheet.get("my_initials") or "").strip().upper(),
        "stale_hours": stale_hours,
        "stale": False,
        "rows": [],
        "names": {},
    }
    if not sheet.get("enabled", True):
        result["error"] = "disabled"
        return result
    try:
        info = resolve_info(cfg)
        source = info["path"]
        kind = info["kind"]
        result["kind"] = kind
        result["warning"] = "" if kind == "synced" else shortcut_warning(cfg)
        if source is None:
            result["error"] = "no tracker sheet found"
            return result
        stat = source.stat()
        copy_dir = Path(str(sheet.get("copy_dir") or (ROOT / "data" / "ape_tracker")))
        try:
            copy_path = snapshot_copy(source, copy_dir)
        except OSError as exc:
            if kind == "synced":
                result["error"] = (
                    "Could not read the synced file yet (OneDrive may still be downloading it): %s" % exc
                )
                return result
            raise
        rows, names = _read(copy_path)
        age_hours = (datetime.now().timestamp() - stat.st_mtime) / 3600.0
        result.update(
            {
                "ok": True,
                "source": str(source),
                "source_mtime": datetime.fromtimestamp(stat.st_mtime)
                .astimezone()
                .isoformat(timespec="seconds"),
                "source_mtime_label": datetime.fromtimestamp(stat.st_mtime).strftime("%d %b %H:%M"),
                "copy": str(copy_path),
                "stale": bool(kind == "downloads" and age_hours > stale_hours),
                "rows": rows,
                "names": names,
            }
        )
    except Exception as exc:
        result["ok"] = False
        result["error"] = str(exc) or exc.__class__.__name__
    return result


def _has_token(text: str, initials: str) -> bool:
    if not initials:
        return False
    tokens = [token.upper() for token in TOKEN_RE.split(text or "") if token]
    return initials.upper() in tokens


def _date_key(value: str) -> int:
    try:
        return -date.fromisoformat(str(value)[:10]).toordinal()
    except (TypeError, ValueError):
        return 0


def _rank(row: dict, my_initials: str) -> tuple:
    return (
        0 if row.get("tab") == "Active" else 1,
        0 if _has_token(row.get("ae", ""), my_initials) else 1,
        _date_key(row.get("assigned", "")),
    )


def match(rows: list[dict], projects: list, my_initials: str = "") -> dict[str, dict]:
    by_no: dict[str, list[dict]] = {}
    by_so: dict[str, list[dict]] = {}
    by_qte: dict[str, list[dict]] = {}
    for row in rows or ():
        keys = row.get("keys") or {}
        for value in keys.get("project_no") or ():
            by_no.setdefault(value, []).append(row)
        for value in keys.get("so") or ():
            by_so.setdefault(value, []).append(row)
        for value in keys.get("qte") or ():
            by_qte.setdefault(value, []).append(row)
    result: dict[str, dict] = {}
    for project in projects or ():
        candidates: list[dict] = []
        orders = set(project.so or ()) | set(project.qte or ())
        if project.project_no:
            for row in by_no.get(project.project_no) or ():
                keys = row.get("keys") or {}
                row_orders = set(keys.get("so") or ()) | set(keys.get("qte") or ())
                if not orders or not row_orders or orders & row_orders:
                    candidates.append(row)
        for value in project.so or ():
            candidates.extend(by_so.get(value) or ())
        for value in project.qte or ():
            candidates.extend(by_qte.get(value) or ())
        if not candidates:
            continue
        result[project.key] = min(candidates, key=lambda row: _rank(row, my_initials))
    return result


def stage_group(status: str) -> int | None:
    found = STATUS_NUMBER_RE.match(status or "")
    if not found:
        return None
    number = int(found.group(1))
    if number == 1:
        return 1
    if 2 <= number <= 5:
        return 2
    if 6 <= number <= 11:
        return 3
    if 12 <= number <= 14:
        return 4
    return None


def hub_stage_group(stage: str) -> int | None:
    return HUB_STAGE_GROUPS.get(str(stage or "").strip().lower())


def stage_mismatch(status: str, stage: str) -> bool:
    left = stage_group(status)
    right = hub_stage_group(stage)
    if left is None or right is None:
        return False
    return left != right


def _key_sets(keys: dict) -> dict[str, set[str]]:
    return {
        "so": set(keys.get("so") or ()),
        "qte": set(keys.get("qte") or ()),
        "project_no": set(keys.get("project_no") or ()),
    }


def _project_keys(projects: list) -> dict[str, set[str]]:
    return {
        "so": {value for project in projects or () for value in project.so or ()},
        "qte": {value for project in projects or () for value in project.qte or ()},
        "project_no": {project.project_no for project in projects or () if project.project_no},
    }


def _pending_keys(pending: dict) -> dict[str, set[str]]:
    sets = {"so": set(), "qte": set(), "project_no": set()}
    for entry in (pending or {}).values():
        parsed = entry.get("parsed") or {}
        sets["so"] |= set(parsed.get("so") or ())
        sets["qte"] |= set(parsed.get("qte") or ())
        if parsed.get("project_no"):
            sets["project_no"].add(str(parsed["project_no"]))
    return sets


def _overlaps(keys: dict[str, set[str]], taken: dict[str, set[str]]) -> bool:
    return any(keys[name] & taken[name] for name in ("so", "qte", "project_no"))


def _level(value: str) -> str:
    found = LEVEL_RE.match(value or "")
    return found.group(1).upper() if found else ""


def _message_id(row: dict) -> str:
    keys = row.get("keys") or {}
    for name in ("so", "qte", "project_no"):
        values = keys.get(name) or []
        if values:
            return "sheet-" + values[0]
    return "sheet-row%s%s" % (row.get("tab") or "", row.get("row") or "")


def sheet_pending(
    rows: list[dict], my_initials: str, projects: list, pending: dict, now_iso: str
) -> dict[str, dict]:
    matched = _project_keys(projects)
    taken = _pending_keys(pending)
    result: dict[str, dict] = {}
    for row in rows or ():
        if not _has_token(row.get("ae", ""), my_initials):
            continue
        group = stage_group(row.get("so_status", ""))
        if group is not None and group >= 4:
            continue
        keys = _key_sets(row.get("keys") or {})
        if _overlaps(keys, matched) or _overlaps(keys, taken):
            continue
        message_id = _message_id(row)
        if message_id in (pending or {}) or message_id in result:
            continue
        so = sorted(keys["so"])
        qte = sorted(keys["qte"])
        numbers = sorted(keys["project_no"])
        ident = so[0] if so else (qte[0] if qte else "")
        level = _level(row.get("level", ""))
        subject = " | ".join(
            part
            for part in (row.get("customer", ""), ident, numbers[0] if numbers else "", row.get("description", ""), level)
            if part
        )
        result[message_id] = {
            "message_id": message_id,
            "subject": "Tracker sheet - " + subject,
            "received": now_iso,
            "parsed": {
                "customer": row.get("customer", ""),
                "so": so,
                "qte": qte,
                "project_no": numbers[0] if numbers else "",
                "level": level,
                "description": row.get("description", ""),
            },
            "sharepoint": "",
            "team": [],
            "checklist": [],
            "suggested_folder": matcher.folder_name_for(
                row.get("customer", ""), so, qte, numbers[0] if numbers else ""
            ),
            "status": "new",
            "source": "sheet",
        }
    return result
