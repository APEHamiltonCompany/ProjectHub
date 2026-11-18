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
    "fabrication": 3,
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
COMPLETED_TAB = "Completed"
COMPLETED_PREFIX = "completed"
SHAREPOINT = "sharepoint"
SHORTCUT_PATTERN = "ape project tracking*.url"
NEW_ROW_DAYS = 45


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


def shortcut_url(path: str | Path) -> str:
    """URL= line of a Windows internet shortcut (.url)."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    for line in text.splitlines():
        if line.strip().lower().startswith("url="):
            return line.split("=", 1)[1].strip()
    return ""


def shortcut_path(cfg: dict) -> Path | None:
    """The SharePoint shortcut to the sheet: ape_sheet.shortcut (a .url file or a folder holding one), or a .url ape_sheet.path."""
    sheet = _sheet_cfg(cfg)
    raw = str(sheet.get("shortcut") or "").strip()
    if raw:
        path = Path(raw)
        if path.is_file() and path.suffix.lower() == ".url":
            return path
        if path.is_dir():
            found = _search_folder(path, SHORTCUT_PATTERN)
            if found is not None:
                return found
    raw = str(sheet.get("path") or "").strip()
    if raw and raw.lower().endswith(".url") and Path(raw).is_file():
        return Path(raw)
    return None


def _copy_dir(cfg: dict) -> Path:
    return Path(str(_sheet_cfg(cfg).get("copy_dir") or (ROOT / "data" / "ape_tracker")))


def newest_copy(copy_dir: Path) -> Path | None:
    try:
        files = [
            entry
            for entry in Path(copy_dir).iterdir()
            if entry.is_file() and entry.name.startswith(COPY_PREFIX) and entry.suffix.lower() == ".xlsx"
        ]
    except OSError:
        return None
    return max(files, key=lambda entry: entry.stat().st_mtime) if files else None


def download_copy(url: str, copy_dir: Path, keep: int = 5) -> Path:
    """Open the SharePoint workbook read-only in a hidden Excel (signed in as the user) and save a local copy."""
    import pythoncom
    import win32com.client

    url = url.split("?", 1)[0]
    copy_dir = Path(copy_dir)
    copy_dir.mkdir(parents=True, exist_ok=True)
    target = copy_dir / ("%s%s.xlsx" % (COPY_PREFIX, datetime.now().strftime("%Y%m%d-%H%M%S")))
    temp = copy_dir / (".ape-download-%d.xlsx" % os.getpid())
    pythoncom.CoInitialize()
    excel = None
    try:
        excel = win32com.client.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        try:
            excel.AskToUpdateLinks = False
        except Exception:
            pass
        workbook = excel.Workbooks.Open(url, UpdateLinks=0, ReadOnly=True, Notify=False, AddToMru=False)
        try:
            workbook.SaveCopyAs(str(temp))
        finally:
            workbook.Close(SaveChanges=False)
    finally:
        if excel is not None:
            try:
                excel.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()
    os.replace(temp, target)
    _prune(copy_dir, keep)
    return target


def resolve_info(cfg: dict) -> dict:
    sheet = _sheet_cfg(cfg)
    shortcut = shortcut_path(cfg)
    if shortcut is not None and shortcut_url(shortcut):
        return {"path": shortcut, "kind": SHAREPOINT}
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
        for sheet_name in wb.sheetnames:
            if sheet_name.strip().lower().startswith(COMPLETED_PREFIX):
                rows.extend(_rows_from(wb[sheet_name], COMPLETED_TAB, names))
        return rows, names
    finally:
        wb.close()


def load(cfg: dict, download: bool | None = None) -> dict:
    """Read the sheet. A SharePoint shortcut is downloaded when download is True, or (None) when the last copy
    is older than ape_sheet.download_hours (default 12)."""
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
        result["warning"] = "" if kind in ("synced", SHAREPOINT) else shortcut_warning(cfg)
        if source is None:
            result["error"] = "no tracker sheet found"
            return result
        copy_dir = _copy_dir(cfg)
        if kind == SHAREPOINT:
            copy_path = newest_copy(copy_dir)
            try:
                max_age = float(sheet.get("download_hours") or 12)
            except (TypeError, ValueError):
                max_age = 12.0
            age = (datetime.now().timestamp() - copy_path.stat().st_mtime) / 3600.0 if copy_path else None
            fetch = (age is None or age > max_age) if download is None else download
            if fetch:
                try:
                    copy_path = download_copy(shortcut_url(source), copy_dir)
                except Exception as exc:
                    if copy_path is None:
                        result["error"] = "Could not download the tracker sheet from SharePoint: %s" % (str(exc) or exc.__class__.__name__)
                        return result
                    result["warning"] = "Could not download a fresh copy from SharePoint (%s); using the last copy." % (
                        str(exc) or exc.__class__.__name__
                    )
            stat = copy_path.stat()
        else:
            stat = source.stat()
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
                "source": shortcut_url(source) if kind == SHAREPOINT else str(source),
                "source_mtime": datetime.fromtimestamp(stat.st_mtime)
                .astimezone()
                .isoformat(timespec="seconds"),
                "source_mtime_label": datetime.fromtimestamp(stat.st_mtime).strftime("%d %b %H:%M"),
                "copy": str(copy_path),
                "stale": bool(kind in ("downloads", SHAREPOINT) and age_hours > stale_hours),
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
    completed = _key_sets({})
    for row in rows or ():
        if row.get("tab") == COMPLETED_TAB:
            for name, values in _key_sets(row.get("keys") or {}).items():
                completed[name] |= values
    try:
        today = datetime.fromisoformat(now_iso).date()
    except (TypeError, ValueError):
        today = date.today()
    result: dict[str, dict] = {}
    for row in rows or ():
        if not _has_token(row.get("ae", ""), my_initials):
            continue
        if row.get("tab") == COMPLETED_TAB or is_ongoing(row.get("so_status", "")):
            continue
        if row.get("tab") == "New" and not _recent(row.get("assigned", ""), today, NEW_ROW_DAYS):
            continue
        group = stage_group(row.get("so_status", ""))
        if group is not None and group >= 4:
            continue
        keys = _key_sets(row.get("keys") or {})
        if _overlaps(keys, matched) or _overlaps(keys, taken) or _overlaps(keys, completed):
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


STATUS_STAGES = ((1, 1, "Pre-Kickoff"), (2, 5, "Pre-Fabrication"), (6, 11, "Fabrication"), (12, 12, "System Test"), (13, 99, "Completion"))
COMPLETION = "Completion"
SYNC_SOURCE = "tracker sheet"
RECENT_DAYS = 30


def status_stage(status: str) -> str:
    found = STATUS_NUMBER_RE.match(status or "")
    if not found:
        return ""
    number = int(found.group(1))
    for low, high, stage in STATUS_STAGES:
        if low <= number <= high:
            return stage
    return ""


def is_ongoing(status: str) -> bool:
    return re.sub(r"[^a-z]", "", str(status or "").lower()) == "ongoing"


def is_hold(status: str) -> bool:
    return str(status or "").strip().lower().startswith("hold")


def _parse_day(value) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _recent(value, today: date, days: int) -> bool:
    day = _parse_day(value)
    return day is not None and (today - day).days <= days


def _rows_by_key(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for row in rows or ():
        for name in ("so", "qte", "project_no"):
            for value in (row.get("keys") or {}).get(name) or ():
                out.setdefault(str(value).upper(), []).append(row)
    return out


def order_state(key: str, rows: list[dict], today: date) -> dict:
    """What the sheet says about one SO/QTE: state is active, done, hold, ignore, unclear, new or off."""
    active = [row for row in rows if row.get("tab") == "Active"]
    if active:
        status = str(active[0].get("so_status") or "").strip()
        if is_ongoing(status):
            return {"state": "ignore", "detail": "On-Going"}
        if is_hold(status):
            return {"state": "hold", "detail": "HOLD"}
        stage = status_stage(status)
        if stage == COMPLETION:
            return {"state": "done", "detail": status}
        if stage:
            return {"state": "active", "detail": status, "stage": stage}
        return {"state": "unclear", "detail": status or "Active Projects, no SO status"}
    if any(row.get("tab") == COMPLETED_TAB for row in rows):
        return {"state": "done", "detail": "on a Completed tab"}
    new = [row for row in rows if row.get("tab") == "New" and _recent(row.get("assigned"), today, NEW_ROW_DAYS)]
    if new:
        return {"state": "new", "detail": "New Projects, assigned %s" % new[0].get("assigned")}
    return {"state": "off", "detail": "not on Active Projects"}


def sync_proposals(rows: list[dict], projects: list[dict], today: date, order: list[str]) -> list[dict]:
    """Stage changes the tracker sheet suggests for each project folder (dicts from snapshot.json)."""
    by_key = _rows_by_key(rows)
    rank = {stage: index for index, stage in enumerate(order or [])}
    out: list[dict] = []
    for project in projects or ():
        sos = [str(value).upper() for value in project.get("so") or []]
        keys = sos or [str(value).upper() for value in list(project.get("qte") or []) + [project.get("project_no") or ""] if value]
        if not keys:
            continue
        found = {key: by_key.get(key) or [] for key in keys}
        if not sos and not any(found.values()):
            continue
        states = [(key, order_state(key, found[key], today)) for key in keys]
        reason = "; ".join("%s: %s" % (key, state["detail"]) for key, state in states)
        kinds = {state["state"] for _key, state in states}
        current = str(project.get("stage") or "") or "Unknown"
        manual = str(project.get("stage_manual") or "")
        by_hand = bool(manual) and str(project.get("stage_manual_source") or "") != SYNC_SOURCE
        note = ""
        actives = [state["stage"] for _key, state in states if state["state"] == "active"]
        if actives:
            proposed = min(actives, key=lambda stage: rank.get(stage, 99))
            checked = current not in rank or rank.get(proposed, 99) >= rank.get(current, -1) or current == COMPLETION
            if not checked:
                note = "emails show a later stage"
        elif kinds & {"hold", "ignore", "unclear", "new"}:
            continue
        else:
            proposed = COMPLETION
            checked = True
            if "done" not in kinds and (
                _recent(project.get("assigned"), today, RECENT_DAYS) or _recent(project.get("last_email"), today, RECENT_DAYS)
            ):
                checked = False
                note = "recent activity: check before completing"
        if proposed == current:
            continue
        if by_hand:
            checked = False
            note = "stage set by hand"
        out.append(
            {
                "key": project.get("key") or "",
                "name": project.get("name") or project.get("key") or "",
                "customer": project.get("customer") or "",
                "current": current,
                "proposed": proposed,
                "reason": reason,
                "checked": bool(checked),
                "note": note,
            }
        )
    out.sort(key=lambda item: (not item["checked"], rank.get(item["proposed"], 99), item["customer"].lower(), item["name"].lower()))
    return out


WEAK_LINK_DAYS = 365


def so_suggestions(rows: list[dict], projects: list[dict], today: date) -> list[dict]:
    """SO numbers the sheet shows for a project's QTE. Ticked when a sheet row lists the QTE and another row with the
    same Project ID (PRJ-...) lists the SO. Unticked when only the 7-digit project number links them (rows assigned in
    the last year only), or when the SO already has its own project folder."""
    by_project_id: dict[str, list[dict]] = {}
    for row in rows:
        project_id = str(row.get("project_id") or "").strip().upper()
        if project_id:
            by_project_id.setdefault(project_id, []).append(row)
    owners: dict[str, dict] = {}
    for project in projects or ():
        for so in project.get("so") or ():
            owners.setdefault(str(so).upper(), project)
    out: list[dict] = []
    for project in projects or ():
        qtes = {str(value).upper() for value in project.get("qte") or ()}
        if not qtes:
            continue
        have = {str(value).upper() for value in project.get("so") or ()}
        dismissed = {str(value).upper() for value in project.get("so_dismissed") or ()}
        found: dict[str, tuple[bool, str]] = {}
        for row in rows:
            keys = row.get("keys") or {}
            row_qtes = qtes & set(keys.get("qte") or ())
            if not row_qtes:
                continue
            qte = sorted(row_qtes)[0]
            for so in keys.get("so") or ():
                found.setdefault(so, (True, "%s and %s are on the same sheet row" % (qte, so)))
            project_id = str(row.get("project_id") or "").strip().upper()
            for other in by_project_id.get(project_id) or () if project_id else ():
                for so in (other.get("keys") or {}).get("so") or ():
                    found.setdefault(so, (True, "%s and %s share Project ID %s on the sheet" % (qte, so, project_id)))
        number = str(project.get("project_no") or "")
        if number and not have:
            for row in rows:
                if number not in ((row.get("keys") or {}).get("project_no") or ()):
                    continue
                if not _recent(row.get("assigned"), today, WEAK_LINK_DAYS):
                    continue
                for so in (row.get("keys") or {}).get("so") or ():
                    found.setdefault(so, (False, "only the project number %s links %s to this project" % (number, so)))
        for so, (strong, reason) in sorted(found.items()):
            if so in have or so in dismissed:
                continue
            owner = owners.get(so)
            note = "" if strong else "check: project number match only"
            if strong and have:
                strong = False
                note = "check: the project already has %s" % ", ".join(sorted(have))
            if owner is not None and owner.get("key") != project.get("key"):
                strong = False
                note = "%s already has its own folder: %s" % (so, owner.get("name") or owner.get("key"))
            out.append(
                {
                    "key": project.get("key") or "",
                    "name": project.get("name") or project.get("key") or "",
                    "customer": project.get("customer") or "",
                    "so": so,
                    "reason": reason,
                    "checked": strong,
                    "note": note,
                }
            )
    out.sort(key=lambda item: (not item["checked"], item["customer"].lower(), item["name"].lower(), item["so"]))
    return out
