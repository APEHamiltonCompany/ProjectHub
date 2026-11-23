from __future__ import annotations

import json
import os
import re
import shutil
from datetime import date, datetime
from pathlib import Path
from xml.etree import ElementTree

from .. import matcher
from . import ape_sheet

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
NS = "http://schemas.microsoft.com/project"
ORDER_RE = re.compile(r"(?<![A-Za-z])(SO|QTE)-(\d+)", re.I)
ROW_PREFIX_RE = re.compile(r"^.*(?<![A-Za-z])(?:SO|QTE)-\d+", re.I)
PM_RE = re.compile(r"^[A-Z]{2,3}(?:\s*\(.*\))?$")
LEVEL_RE = re.compile(r"(?<![A-Za-z0-9])L([1-4])(R?)(?![A-Za-z0-9])", re.I)
KICKOFF_RE = re.compile(r"Kick-?\s*Off|KO\s*Call|KO\s*Email", re.I)
CLOSEOUT_RE = re.compile(r"AutoE Close\s*Out", re.I)
ASSIGNED_PREFIX = "Assigned |"
TRT_PREFIX = "TRT |"
DEFAULT_PREFIXES = ()
DEFAULT_SKIP = ("[", "External Lab Space Requests")
CACHE_NAME = "schedule_cache.json"
FINISHED_DAYS = 30
COM_PROGID = "MSProject.Application"
MPXJ_ERROR = "MPXJ is not installed: pip install mpxj JPype1 and install Java 11+"


def _conf(cfg: dict | None) -> dict:
    return dict((cfg or {}).get("ms_project") or {})


def _source_path(cfg: dict | None) -> str:
    raw = str((cfg or {}).get("ms_project_path") or "").strip()
    if raw:
        return raw
    return str(_conf(cfg).get("path") or "").strip()


def _cache_path(cfg: dict | None) -> Path:
    raw = str(_conf(cfg).get("cache_path") or "").strip()
    return Path(raw) if raw else ROOT / "output" / CACHE_NAME


def _copy_dir(cfg: dict | None) -> Path:
    raw = str(_conf(cfg).get("copy_dir") or "").strip()
    return Path(raw) if raw else ROOT / "output" / "schedule"


def _iso(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return ""


def _pct(value) -> int:
    try:
        return int(round(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _ordinal(value) -> int:
    text = _iso(value)
    return date.fromisoformat(text).toordinal() if text else 0


def _squash(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _text(node, name: str) -> str:
    found = node.find("{%s}%s" % (NS, name))
    if found is None or found.text is None:
        return ""
    return found.text.strip()


def read_mspdi(path) -> dict:
    """MSPDI XML (Project "Save as XML"): standard library only."""
    root = ElementTree.parse(str(path)).getroot()
    tasks: list[dict] = []
    for node in root.iter("{%s}Task" % NS):
        uid = _text(node, "UID")
        name = _text(node, "Name")
        if not uid or uid == "0" or not name:
            continue
        tasks.append(
            {
                "uid": uid,
                "name": name,
                "outline_level": _int(_text(node, "OutlineLevel"), 1),
                "summary": _text(node, "Summary") in ("1", "true", "True"),
                "start": _iso(_text(node, "Start")),
                "finish": _iso(_text(node, "Finish")),
                "pct": _pct(_text(node, "PercentComplete")),
                "created": _iso(_text(node, "CreateDate")),
                "resource_ids": [],
            }
        )
    resources: list[dict] = []
    for node in root.iter("{%s}Resource" % NS):
        uid = _text(node, "UID")
        if not uid or uid == "0":
            continue
        resources.append(
            {"id": uid, "name": _text(node, "Name"), "email": _text(node, "EmailAddress")}
        )
    by_uid = {task["uid"]: task for task in tasks}
    for node in root.iter("{%s}Assignment" % NS):
        task = by_uid.get(_text(node, "TaskUID"))
        resource = _text(node, "ResourceUID")
        if task is None or not resource or resource == "0":
            continue
        if resource not in task["resource_ids"]:
            task["resource_ids"].append(resource)
    return {"tasks": tasks, "resources": resources}


def find_java_home(cfg: dict | None = None) -> str:
    """cfg ms_project.java_home, JAVA_HOME, %LOCALAPPDATA%/ProjectHub/jre/*, then java on PATH."""
    for candidate in (_conf(cfg).get("java_home"), os.environ.get("JAVA_HOME")):
        text = str(candidate or "").strip()
        if text and (Path(text) / "bin" / "java.exe").is_file():
            return text
    local = str(os.environ.get("LOCALAPPDATA") or "").strip()
    if local:
        try:
            for entry in sorted((Path(local) / "ProjectHub" / "jre").iterdir()):
                if (entry / "bin" / "java.exe").is_file():
                    return str(entry)
        except OSError:
            pass
    found = shutil.which("java")
    if found:
        return str(Path(found).resolve().parent.parent)
    return ""


def read_mpxj(path, java_home: str = "") -> dict:
    """MPXJ through JPype. Needs Java 11+ and `pip install mpxj JPype1`."""
    try:
        import jpype
        import mpxj  # noqa: F401
    except ImportError:
        raise RuntimeError(MPXJ_ERROR)
    home = java_home or find_java_home({})
    if home:
        os.environ["JAVA_HOME"] = home
    if not jpype.isJVMStarted():
        try:
            jpype.startJVM()
        except Exception as exc:
            raise RuntimeError("Java runtime not found: %s" % (str(exc) or exc.__class__.__name__))
    try:
        from org.mpxj.reader import UniversalProjectReader
    except Exception:
        from net.sf.mpxj.reader import UniversalProjectReader
    project = UniversalProjectReader().read(str(path))
    tasks: list[dict] = []
    for task in project.getTasks() or ():
        if task is None:
            continue
        uid = str(task.getUniqueID())
        name = str(task.getName() or "").strip()
        if uid == "0" or not name:
            continue
        resource_ids: list[str] = []
        for assignment in task.getResourceAssignments() or ():
            if assignment is None or assignment.getResource() is None:
                continue
            rid = str(assignment.getResource().getUniqueID())
            if rid not in resource_ids:
                resource_ids.append(rid)
        tasks.append(
            {
                "uid": uid,
                "name": name,
                "outline_level": _int(task.getOutlineLevel(), 1),
                "summary": bool(task.getSummary()),
                "start": _iso(task.getStart()),
                "finish": _iso(task.getFinish()),
                "pct": _pct(task.getPercentageComplete()),
                "created": _iso(task.getCreateDate()),
                "resource_ids": resource_ids,
            }
        )
    resources: list[dict] = []
    for resource in project.getResources() or ():
        if resource is None:
            continue
        uid = str(resource.getUniqueID())
        if uid == "0":
            continue
        resources.append(
            {
                "id": uid,
                "name": str(resource.getName() or "").strip(),
                "email": str(resource.getEmailAddress() or "").strip(),
            }
        )
    return {"tasks": tasks, "resources": resources}


def _com_available() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg
    except ImportError:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, COM_PROGID):
            return True
    except OSError:
        return False


def read_com(path) -> dict:
    """MS Project through win32com (needs MS Project installed)."""
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    app = None
    try:
        app = win32com.client.DispatchEx(COM_PROGID)
        try:
            app.Visible = False
        except Exception:
            pass
        app.FileOpenEx(str(path), ReadOnly=True)
        project = app.ActiveProject
        tasks: list[dict] = []
        for task in project.Tasks:
            if task is None:
                continue
            uid = str(task.UniqueID)
            name = str(task.Name or "").strip()
            if uid == "0" or not name:
                continue
            resource_ids: list[str] = []
            try:
                assignments = task.Assignments
            except Exception:
                assignments = None
            for assignment in assignments or ():
                if assignment is None:
                    continue
                rid = str(assignment.ResourceUniqueID)
                if rid and rid != "0" and rid not in resource_ids:
                    resource_ids.append(rid)
            tasks.append(
                {
                    "uid": uid,
                    "name": name,
                    "outline_level": _int(task.OutlineLevel, 1),
                    "summary": bool(task.Summary),
                    "start": _iso(task.Start),
                    "finish": _iso(task.Finish),
                    "pct": _pct(task.PercentComplete),
                    "created": _iso(task.Created),
                    "resource_ids": resource_ids,
                }
            )
        resources: list[dict] = []
        for resource in project.Resources:
            if resource is None:
                continue
            uid = str(resource.UniqueID)
            if uid == "0":
                continue
            resources.append(
                {
                    "id": uid,
                    "name": str(resource.Name or "").strip(),
                    "email": str(resource.EMailAddress or "").strip(),
                }
            )
        return {"tasks": tasks, "resources": resources}
    finally:
        if app is not None:
            try:
                app.FileCloseEx(0)
            except Exception:
                pass
            try:
                app.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()


def read_file(path, cfg: dict | None = None) -> dict:
    target = Path(path)
    suffix = target.suffix.lower()
    if suffix == ".xml":
        return read_mspdi(target)
    if suffix in (".mpp", ".mpt"):
        try:
            return read_mpxj(target, find_java_home(cfg))
        except RuntimeError as exc:
            if not _com_available():
                raise
            try:
                return read_com(target)
            except Exception:
                raise exc
    raise RuntimeError("unsupported schedule file: %s" % target.name)


def _prefix_of(name: str, prefixes: list[str]) -> str:
    text = _squash(name)
    for prefix in prefixes:
        if text.startswith(_squash(prefix)):
            return prefix
    return ""


def _skipped(name: str, prefixes: list[str]) -> bool:
    return bool(_prefix_of(name, prefixes))


def _orders(text, kind: str = "") -> list[str]:
    found: list[str] = []
    for match in ORDER_RE.finditer(str(text or "")):
        if kind and match.group(1).upper() != kind:
            continue
        value = "%s-%s" % (match.group(1).upper(), match.group(2))
        if value not in found:
            found.append(value)
    return found


def _row_name(name) -> str:
    text = re.sub(r"\s+", " ", str(name or "")).strip()
    match = ROW_PREFIX_RE.match(text)
    if match:
        text = re.sub(r"^[\s\-]+", "", text[match.end():]).strip()
    return text or re.sub(r"\s+", " ", str(name or "")).strip()


def _is_level(segment: str) -> bool:
    return bool(re.fullmatch(r"L[1-4]R?", str(segment or "").strip(), re.I))


def _summary_parts(name: str) -> tuple[str, str, str, str]:
    parts = [part.strip() for part in str(name or "").split(" - ") if part.strip()]
    customer = parts[0] if parts else ""
    rest = parts[1:]
    match = LEVEL_RE.search(str(name or ""))
    level = "L%s%s" % (match.group(1), match.group(2).upper()) if match else ""
    pm = ""
    if rest and PM_RE.match(rest[-1]):
        pm = rest.pop()
    elif len(rest) >= 2 and _is_level(rest[-1]) and PM_RE.match(rest[-2]):
        pm = rest.pop(-2)
    return customer, pm, level, " - ".join(rest)


def _is_orders(segment: str) -> bool:
    rest = ORDER_RE.sub("", segment)
    return not re.search(r"[A-Za-z]", rest)


def _description(segments: list[str]) -> str:
    for segment in reversed(segments):
        if not segment or _is_orders(segment):
            continue
        text = ORDER_RE.sub("", segment)
        text = re.sub(r"\s*[,;&/]\s*", " ", text)
        return re.sub(r"\s+", " ", text).strip(" -,")
    return ""


def _people(tasks: list[dict], resources: dict[str, dict]) -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()
    for task in tasks:
        for rid in task.get("resource_ids") or ():
            resource = resources.get(str(rid))
            if resource is None:
                continue
            key = str(resource.get("id") or "")
            if key in seen:
                continue
            seen.add(key)
            found.append(
                {"name": str(resource.get("name") or ""), "email": str(resource.get("email") or "")}
            )
    return found


def _resources(raw: dict) -> dict[str, dict]:
    return {str(item.get("id") or ""): item for item in (raw or {}).get("resources") or ()}


def _assignment_date(so: list[str], qte: list[str], assign_rows: list[dict]) -> str:
    wanted = set(so) | set(qte)
    dates = [
        _iso(row.get("date"))
        for row in assign_rows or ()
        if wanted & (set(row.get("so") or ()) | set(row.get("qte") or ()))
    ]
    dates = [value for value in dates if value]
    return min(dates) if dates else ""


def _kickoff_date(children: list[dict]) -> str:
    dates = [
        _iso(child.get("start"))
        for child in children
        if KICKOFF_RE.search(_row_name(child.get("name")))
    ]
    dates = [value for value in dates if value]
    return min(dates) if dates else ""


def _project(
    task: dict,
    children: list[dict],
    resources: dict[str, dict],
    assign_rows: list[dict],
    kind: str = "assigned",
    prefix: str = "",
) -> dict:
    name = str(task.get("name") or "").strip()
    body = re.sub(r"^\s*" + re.escape(prefix) + r"\s*", "", name, flags=re.I).strip() if prefix else name
    block = [task] + list(children)
    so: list[str] = []
    qte: list[str] = []
    for item in block:
        for value in _orders(item.get("name")):
            bucket = so if value.startswith("SO-") else qte
            if value not in bucket:
                bucket.append(value)
    customer, pm, level, description = _summary_parts(body)
    rows = [
        {
            "uid": str(child.get("uid") or ""),
            "name": str(child.get("name") or "").strip(),
            "start": _iso(child.get("start")),
            "finish": _iso(child.get("finish")),
            "pct": _pct(child.get("pct")),
            "people": _people([child], resources),
        }
        for child in children
    ]
    return {
        "uid": str(task.get("uid") or ""),
        "name": name,
        "kind": kind,
        "customer": customer,
        "pm": pm,
        "level": level,
        "so": so,
        "qte": qte,
        "description": description,
        "created": _assignment_date(so, qte, assign_rows) or _kickoff_date(children) or _iso(task.get("start")),
        "start": _iso(task.get("start")),
        "finish": _iso(task.get("finish")),
        "people": _people(block, resources),
        "rows": rows,
    }


def assignment_rows(raw: dict) -> list[dict]:
    """'Assigned | ...' rows under [NEW PROJECT SCHEDULING]: one per assignment date."""
    resources = _resources(raw)
    wanted = [_squash(ASSIGNED_PREFIX)]
    found: list[dict] = []
    for task in (raw or {}).get("tasks") or ():
        name = str(task.get("name") or "").strip()
        if task.get("summary") or not _prefix_of(name, wanted):
            continue
        body = re.sub(r"^\s*assigned\s*\|\s*", "", name, flags=re.I).strip()
        parts = [part.strip() for part in body.split(" - ") if part.strip()]
        found.append(
            {
                "uid": str(task.get("uid") or ""),
                "name": name,
                "customer": parts[0] if parts else "",
                "so": _orders(body, "SO"),
                "qte": _orders(body, "QTE"),
                "description": _description(parts[1:]),
                "date": _iso(task.get("start")),
                "people": _people([task], resources),
            }
        )
    return found


def normalise(raw: dict, prefixes=None, skip=None, assign_rows=None) -> list[dict]:
    """Level-1 summary tasks carrying an SO/QTE number become projects; their descendants become rows."""
    wanted = [_squash(prefix) for prefix in (DEFAULT_PREFIXES if prefixes is None else prefixes) if _squash(prefix)]
    skipped = [_squash(prefix) for prefix in (DEFAULT_SKIP if skip is None else skip) if _squash(prefix)]
    resources = _resources(raw)
    tasks = list((raw or {}).get("tasks") or ())
    rows = assignment_rows(raw) if assign_rows is None else list(assign_rows)
    projects: list[dict] = []
    index = 0
    while index < len(tasks):
        task = tasks[index]
        level = _int(task.get("outline_level"), 1)
        cursor = index + 1
        while cursor < len(tasks) and _int(tasks[cursor].get("outline_level"), 1) > level:
            cursor += 1
        block = tasks[index:cursor]
        name = str(task.get("name") or "").strip()
        if level == 1 and task.get("summary"):
            if _skipped(name, skipped):
                for child in block[1:]:
                    if _int(child.get("outline_level"), 1) != 2 or child.get("summary"):
                        continue
                    prefix = _prefix_of(child.get("name") or "", wanted)
                    if prefix:
                        projects.append(_project(child, [], resources, rows, kind="trt", prefix=prefix))
            elif any(_orders(item.get("name")) for item in block):
                children = [item for item in block[1:] if not item.get("summary")]
                projects.append(_project(task, children, resources, rows))
        index = cursor
    return projects


def _prune(copy_dir: Path, keep: int) -> None:
    try:
        files = [
            entry
            for entry in Path(copy_dir).iterdir()
            if entry.is_file() and entry.name.startswith(ape_sheet.COPY_PREFIX)
        ]
    except OSError:
        return
    files.sort(key=lambda entry: entry.name, reverse=True)
    for old in files[max(1, int(keep)):]:
        try:
            old.unlink()
        except OSError:
            pass


def _snapshot(source: Path, copy_dir: Path, keep: int) -> Path:
    copy = ape_sheet.snapshot_copy(source, copy_dir, keep)
    target = copy.with_suffix(source.suffix.lower() or copy.suffix)
    if target != copy:
        os.replace(copy, target)
    _prune(copy_dir, keep)
    return target


def _read_cache(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_cache(path: Path, data: dict) -> None:
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def _reader_name(path) -> str:
    return "mspdi" if Path(path).suffix.lower() == ".xml" else "mpxj"


def _fallback(cached: dict, result: dict, message: str) -> dict:
    if cached and cached.get("ok"):
        stale = dict(cached)
        stale["stale"] = True
        stale["error"] = message
        return stale
    result["ok"] = False
    result["error"] = message
    return result


def load(cfg: dict, reader=None) -> dict:
    """Read the schedule (through a dated copy) and cache the normalised result."""
    conf = _conf(cfg)
    result = {
        "ok": False,
        "error": "",
        "stale": False,
        "source": "",
        "copy": "",
        "file_mtime": "",
        "size": 0,
        "reader": "",
        "resources": [],
        "projects": [],
        "assignments": [],
    }
    raw_path = _source_path(cfg)
    if not raw_path:
        result["error"] = "not configured"
        return result
    source = Path(raw_path)
    result["source"] = str(source)
    cache_path = _cache_path(cfg)
    cached = _read_cache(cache_path)
    try:
        stat = source.stat()
    except OSError as exc:
        return _fallback(cached, result, "cannot read %s: %s" % (source, exc))
    result["file_mtime"] = datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds")
    result["size"] = int(stat.st_size)
    if (
        cached.get("ok")
        and cached.get("size") == result["size"]
        and cached.get("file_mtime") == result["file_mtime"]
    ):
        fresh = dict(cached)
        fresh["reader"] = "cache"
        fresh["stale"] = False
        return fresh
    try:
        copy_path = _snapshot(source, _copy_dir(cfg), _int(conf.get("keep"), 5))
        raw = (reader or read_file)(copy_path, cfg)
        prefixes = [TRT_PREFIX] if conf.get("include_trt") else []
        assign_rows = assignment_rows(raw)
        projects = normalise(raw, prefixes, conf.get("skip"), assign_rows)
    except Exception as exc:
        return _fallback(cached, result, str(exc) or exc.__class__.__name__)
    result.update(
        {
            "ok": True,
            "copy": str(copy_path),
            "reader": _reader_name(copy_path),
            "resources": list(raw.get("resources") or ()),
            "projects": projects,
            "assignments": assign_rows,
        }
    )
    _write_cache(cache_path, result)
    return result


def is_me(person: dict, cfg: dict) -> bool:
    conf = _conf(cfg)
    me = dict((cfg or {}).get("me") or {})
    resource = str(conf.get("resource") or "").strip()
    email = str((person or {}).get("email") or "").strip().lower()
    name = _squash((person or {}).get("name"))
    if "@" in resource:
        if email and email == resource.lower():
            return True
    elif resource and name and name == _squash(resource):
        return True
    me_email = str(me.get("email") or "").strip().lower()
    if me_email and email and email == me_email:
        return True
    me_name = _squash(me.get("name"))
    return bool(me_name and name and name == me_name)


def _rank(item: dict, cfg: dict, project=None) -> tuple:
    mine = 0 if any(is_me(person, cfg) for person in item.get("people") or ()) else 1
    customer = _squash(getattr(project, "customer", ""))
    item_customer = _squash(item.get("customer"))
    customer_match = 1 if customer and item_customer and (
        customer.startswith(item_customer) or item_customer.startswith(customer)
    ) else 0
    wanted = {
        str(value).upper()
        for value in list(getattr(project, "so", None) or ()) + list(getattr(project, "qte", None) or ())
    }
    have = {str(value).upper() for value in list(item.get("so") or ()) + list(item.get("qte") or ())}
    overlap = len(wanted & have)
    return (
        0 if item.get("kind") == "assigned" else 1,
        -customer_match,
        -overlap,
        mine,
        -_ordinal(item.get("finish")),
    )


def match(data: dict, projects: list, cfg: dict, links: dict | None = None) -> dict[str, dict]:
    by_so: dict[str, list[dict]] = {}
    by_qte: dict[str, list[dict]] = {}
    for item in (data or {}).get("projects") or ():
        for value in item.get("so") or ():
            by_so.setdefault(str(value).upper(), []).append(item)
        for value in item.get("qte") or ():
            by_qte.setdefault(str(value).upper(), []).append(item)
    result: dict[str, dict] = {}
    for project in projects or ():
        if (links or {}).get(project.key) == "off":
            continue
        candidates: list[dict] = []
        for value in project.so or ():
            candidates.extend(by_so.get(str(value).upper()) or ())
        for value in project.qte or ():
            candidates.extend(by_qte.get(str(value).upper()) or ())
        if not candidates:
            continue
        unique = list({id(item): item for item in candidates}.values())
        result[project.key] = min(unique, key=lambda item: _rank(item, cfg, project))
    return result


def schedule_rows(schedule_project: dict | None) -> list[dict]:
    return [
        {
            "name": _row_name(row.get("name")),
            "start": row.get("start") or "",
            "finish": row.get("finish") or "",
            "pct": _pct(row.get("pct")),
        }
        for row in (schedule_project or {}).get("rows") or ()
    ]


def _finished(item: dict, today: date) -> bool:
    rows = list(item.get("rows") or ())
    if rows and all(_pct(row.get("pct")) == 100 for row in rows):
        return True
    for row in rows:
        if _pct(row.get("pct")) == 100 and CLOSEOUT_RE.search(row.get("name") or ""):
            return True
    finish = _ordinal(item.get("finish"))
    return bool(finish) and (today.toordinal() - finish) > FINISHED_DAYS


def schedule_pending(
    data: dict, cfg: dict, projects: list, pending: dict, now_iso: str
) -> dict[str, dict]:
    """Assigned schedule projects that are mine and not in the vault yet, as pending-assignment dicts."""
    matched = ape_sheet._project_keys(projects)
    taken = ape_sheet._pending_keys(pending)
    try:
        today = datetime.fromisoformat(now_iso).date()
    except (TypeError, ValueError):
        today = date.today()
    result: dict[str, dict] = {}
    for item in (data or {}).get("projects") or ():
        if item.get("kind") != "assigned":
            continue
        if not any(is_me(person, cfg) for person in item.get("people") or ()):
            continue
        keys = {"so": set(item.get("so") or ()), "qte": set(item.get("qte") or ()), "project_no": set()}
        if ape_sheet._overlaps(keys, matched) or ape_sheet._overlaps(keys, taken):
            continue
        message_id = "schedule:%s" % (item.get("uid") or "")
        if message_id in (pending or {}) or message_id in result:
            continue
        if _finished(item, today):
            continue
        so = sorted(keys["so"])
        qte = sorted(keys["qte"])
        ident = so[0] if so else (qte[0] if qte else "")
        subject = " | ".join(
            part for part in (item.get("customer") or "", ident, item.get("description") or "") if part
        )
        result[message_id] = {
            "message_id": message_id,
            "subject": "Schedule - " + subject,
            "received": item.get("created") or now_iso,
            "parsed": {
                "customer": item.get("customer") or "",
                "so": so,
                "qte": qte,
                "project_no": "",
                "level": "",
                "description": item.get("description") or "",
            },
            "sharepoint": "",
            "team": [],
            "checklist": [],
            "suggested_folder": matcher.folder_name_for(item.get("customer") or "", so, qte, ""),
            "status": "new",
            "source": "schedule",
        }
    return result
