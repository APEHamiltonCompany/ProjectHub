"""Team status sharing on R: (manager view).

Layout under team.shared_dir (default R:/APE_AE/Project):
- team.js                    roster, appended by each hub on its first publish; roles are edited by hand
- <Name>/status.json|.js     one writer: that person's hub
- _requests/<id>.json        reassign request, written by a manager or principal engineer hub
- _requests/<id>.<who>.json  replies: the new owner ("imported") or the previous owner ("acknowledged")
- Team Status.html           read-only page for PMs (opened from R:, loads the .js files with script tags)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import uuid
from datetime import date, datetime
from pathlib import Path

from . import backup, matcher

HERE = Path(__file__).resolve().parent
PAGE_SOURCE = HERE / "team_page" / "Team Status.html"
VIEW_SOURCE = HERE / "team_page" / "team_view.js"
ROSTER_NAME = "team.js"
REQUESTS_DIR = "_requests"
DEFAULT_SHARED = "R:/APE_AE/Project"
REASSIGN_ROLES = ("manager", "principal")
ROSTER_PREFIX = "window.TEAM_ROSTER = "
PMS_PREFIX = "window.TEAM_PMS = "
# Known PM initials (MS Project "PM" column). team.js TEAM_PMS adds to / overrides these.
KNOWN_PMS = {
    "AA": "Aman Aman",
    "CD": "Chris Derby",
    "CH": "Cindy Heinrich",
    "DM": "Diane Martin",
    "MS": "Matt Sturdivant",
    "ST": "Scott Thompson",
}
STATUS_PREFIX = "window.TEAM_STATUS = window.TEAM_STATUS || {};\nwindow.TEAM_STATUS[%s] = "
SCHEMA = 1


def conf(cfg: dict) -> dict:
    raw = (cfg or {}).get("team") or {}
    return raw if isinstance(raw, dict) else {}


def shared_dir(cfg: dict) -> Path:
    return Path(str(conf(cfg).get("shared_dir") or DEFAULT_SHARED))


def me(cfg: dict) -> dict:
    raw = (cfg or {}).get("me") or {}
    return {"name": str(raw.get("name") or "").strip(), "email": str(raw.get("email") or "").strip()}


def person_dir_name(name: str) -> str:
    return matcher.sanitize_name(name or "") or "Unknown"


def same_person(entry: dict, who: dict) -> bool:
    email = str(who.get("email") or "").lower()
    if email and str(entry.get("email") or "").lower() == email:
        return True
    name = re.sub(r"\s+", " ", str(who.get("name") or "")).strip().lower()
    return bool(name) and re.sub(r"\s+", " ", str(entry.get("name") or "")).strip().lower() == name


def available(cfg: dict) -> tuple[bool, str]:
    """Team files only move while R: is up: never from the backup vault."""
    if (cfg.get("offline") or {}).get("active"):
        return False, "working offline; waits until R: is back"
    root = shared_dir(cfg)
    if not backup.reachable(root):
        return False, "%s is not reachable; waits until R: is back" % root
    return True, ""


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------- roster

def read_team_file(cfg: dict) -> dict:
    """{"TEAM_ROSTER": [...], "TEAM_PMS": {...}} from team.js (one `window.NAME = <json>;` per variable)."""
    try:
        text = (shared_dir(cfg) / ROSTER_NAME).read_text(encoding="utf-8")
    except OSError:
        return {}
    out = {}
    parts = re.split(r"^window\.(TEAM_[A-Z]+)\s*=\s*", text, flags=re.M)
    for name, body in zip(parts[1::2], parts[2::2]):
        try:
            out[name] = json.loads(body.strip().rstrip(";"))
        except ValueError:
            pass
    return out


def read_roster(cfg: dict) -> list[dict]:
    data = read_team_file(cfg).get("TEAM_ROSTER")
    return [row for row in data or [] if isinstance(row, dict) and row.get("name")]


def file_pms(data: dict) -> dict:
    pms = data.get("TEAM_PMS")
    return {str(k).strip().upper(): str(v).strip() for k, v in pms.items() if k and v} if isinstance(pms, dict) else {}


def read_pms(cfg: dict) -> dict:
    return {**KNOWN_PMS, **file_pms(read_team_file(cfg))}


def ensure_pms(cfg: dict) -> None:
    """Write the known PM names into team.js when it lacks any of them (keeps hand edits)."""
    data = read_team_file(cfg)
    have = file_pms(data)
    if "TEAM_ROSTER" not in data or all(key in have for key in KNOWN_PMS):
        return
    _write_atomic(shared_dir(cfg) / ROSTER_NAME, roster_text(read_roster(cfg), {**KNOWN_PMS, **have}))


def roster_text(rows: list[dict], pms: dict | None = None) -> str:
    text = ROSTER_PREFIX + json.dumps(rows, indent=2) + ";\n"
    if pms:
        text += PMS_PREFIX + json.dumps(pms, indent=2, sort_keys=True) + ";\n"
    return text


def pm_name(value: str, pms: dict) -> str:
    initials = pm_initials(value)
    return pms.get(initials.upper(), initials)


def ensure_member(cfg: dict, attempts: int = 3) -> dict:
    """Append me to team.js when missing (first publish). Rewrites, re-reads and retries if another hub raced us."""
    who = me(cfg)
    if not who["name"]:
        return {}
    for _attempt in range(attempts):
        rows = read_roster(cfg)
        found = next((row for row in rows if same_person(row, who)), None)
        if found is not None:
            return found
        entry = {"name": who["name"], "email": who["email"], "folder": person_dir_name(who["name"]), "role": ""}
        _write_atomic(shared_dir(cfg) / ROSTER_NAME, roster_text(rows + [entry], read_pms(cfg)))
        time.sleep(0.2)
        if any(same_person(row, who) for row in read_roster(cfg)):
            return entry
    return {}


def can_reassign(cfg: dict, roster: list[dict] | None = None) -> bool:
    who = me(cfg)
    rows = read_roster(cfg) if roster is None else roster
    return any(same_person(row, who) and str(row.get("role") or "").lower() in REASSIGN_ROLES for row in rows)


def manager_mode(cfg: dict, roster: list[dict] | None = None) -> bool:
    return bool(conf(cfg).get("manager_mode")) or can_reassign(cfg, roster)


# ---------------------------------------------------------------- status

def _iso(value) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def pm_initials(value: str) -> str:
    return re.sub(r"\s*\(.*\)\s*$", "", str(value or "")).strip()


def project_status(row: dict, today: date, pms: dict | None = None) -> dict:
    tasks = [task for task in row.get("tasks") or [] if task.get("id")]
    open_tasks = [task for task in tasks if (task.get("mark") or " ") == " "]
    overdue = [task for task in open_tasks if _iso(task.get("due")) and _iso(task.get("due")) < today]
    upcoming = sorted((task for task in open_tasks if _iso(task.get("due"))), key=lambda task: task["due"])
    sched = row.get("schedule") or {}
    starts = [task.get("start") for task in tasks if task.get("start")]
    dues = [task.get("due") for task in tasks if task.get("due")]
    sheet = row.get("sheet") or {}
    return {
        "key": row.get("key") or "",
        "name": row.get("name") or "",
        "customer": row.get("customer") or "",
        "so": list(row.get("so") or []),
        "qte": list(row.get("qte") or []),
        "project_no": row.get("project_no") or "",
        "level": row.get("level") or sched.get("level") or "",
        "stage": row.get("stage") or "",
        "pm": pm_name(str(sched.get("pm") or sheet.get("pm_name") or ""), pms or {}),
        "start": sched.get("start") or (min(starts) if starts else ""),
        "finish": sched.get("finish") or (max(dues) if dues else ""),
        "dates_from": "schedule" if sched.get("start") or sched.get("finish") else ("tasks" if starts or dues else ""),
        "schedule_project": row.get("schedule_project") or "",
        "tasks_total": len(tasks),
        "tasks_done": len([task for task in tasks if (task.get("mark") or " ") != " "]),
        "overdue": [{"title": task.get("title") or "", "due": task.get("due")} for task in overdue],
        "next_due": ({"title": upcoming[0].get("title") or "", "due": upcoming[0]["due"]} if upcoming else None),
        "ship_date": row.get("ship_date") or "",
        "flags": list(row.get("owner_flags") or []),
        "reassigned_to": row.get("reassigned_to") or "",
        "folder": row.get("folder") or "",
    }


def build_status(cfg: dict, snapshot: dict, archived: dict, today: date) -> dict:
    who = me(cfg)
    pms = read_pms(cfg)
    rows = [
        project_status(row, today, pms)
        for row in snapshot.get("projects") or []
        if row.get("key") not in archived
    ]
    return {
        "schema": SCHEMA,
        "name": who["name"],
        "email": who["email"],
        "generated": snapshot.get("generated") or datetime.now().astimezone().isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "projects": rows,
    }


def publish(cfg: dict, snapshot: dict, archived: dict, today: date) -> dict:
    """Write my status.json/.js, join the roster on first publish, and drop the PM page in place."""
    if not conf(cfg).get("publish"):
        return {"ok": False, "skipped": "team.publish is off"}
    who = me(cfg)
    if not who["name"]:
        return {"ok": False, "skipped": "me.name is not set"}
    ok, why = available(cfg)
    if not ok:
        return {"ok": False, "skipped": why}
    root = shared_dir(cfg)
    member = ensure_member(cfg)
    try:
        ensure_pms(cfg)
    except OSError:
        pass
    outgoing = {request.get("key"): request.get("to") for request in my_transfers(cfg)["outgoing"]}
    for row in snapshot.get("projects") or []:
        if row.get("key") in outgoing:
            row["reassigned_to"] = outgoing[row["key"]]
    status = build_status(cfg, snapshot, archived, today)
    folder = root / str(member.get("folder") or person_dir_name(who["name"]))
    _write_atomic(folder / "status.json", json.dumps(status, indent=2) + "\n")
    _write_atomic(folder / "status.js", STATUS_PREFIX % json.dumps(who["name"]) + json.dumps(status) + ";\n")
    for source in (PAGE_SOURCE, VIEW_SOURCE):
        target = root / source.name
        try:
            if source.is_file() and (not target.is_file() or target.read_bytes() != source.read_bytes()):
                shutil.copyfile(source, target)
        except OSError:
            pass
    return {"ok": True, "folder": str(folder), "projects": len(status["projects"]), "joined": bool(member)}


def load_statuses(cfg: dict, roster: list[dict] | None = None) -> list[dict]:
    rows = read_roster(cfg) if roster is None else roster
    out = []
    for member in rows:
        path = shared_dir(cfg) / str(member.get("folder") or person_dir_name(member.get("name") or "")) / "status.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {"name": member.get("name"), "projects": [], "missing": True}
        data["role"] = member.get("role") or ""
        data.setdefault("name", member.get("name"))
        out.append(data)
    return out


# ---------------------------------------------------------------- reassignment

def _requests(cfg: dict) -> Path:
    return shared_dir(cfg) / REQUESTS_DIR


def write_request(cfg: dict, project: dict, from_name: str, to_name: str, roster: list[dict] | None = None) -> dict:
    rows = read_roster(cfg) if roster is None else roster
    if not can_reassign(cfg, rows):
        raise PermissionError("only the team manager or the principal engineer can reassign")
    ok, why = available(cfg)
    if not ok:
        raise OSError(why)
    if not any(same_person(row, {"name": to_name}) for row in rows):
        raise ValueError("%s is not on the team roster" % to_name)
    if same_person({"name": from_name}, {"name": to_name}):
        raise ValueError("the project already belongs to %s" % to_name)
    request = {
        "id": datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6],
        "key": str(project.get("key") or ""),
        "name": str(project.get("name") or ""),
        "customer": str(project.get("customer") or ""),
        "so": list(project.get("so") or []),
        "qte": list(project.get("qte") or []),
        "project_no": str(project.get("project_no") or ""),
        "from": from_name,
        "from_folder": str(project.get("folder") or ""),
        "to": to_name,
        "by": me(cfg)["name"],
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    _write_atomic(_requests(cfg) / (request["id"] + ".json"), json.dumps(request, indent=2) + "\n")
    return request


def _reply_name(request_id: str, who: str) -> str:
    return "%s.%s.json" % (request_id, re.sub(r"[^a-z0-9]+", "_", who.lower()).strip("_"))


def list_requests(cfg: dict, roster: list[dict] | None = None) -> list[dict]:
    """Valid requests (writer on the roster as manager/principal) with their replies attached."""
    rows = read_roster(cfg) if roster is None else roster
    folder = _requests(cfg)
    try:
        names = sorted(entry.name for entry in folder.iterdir() if entry.suffix == ".json")
    except OSError:
        return []
    out = []
    for name in names:
        if name.count(".") != 1:
            continue
        try:
            request = json.loads((folder / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        by = {"name": request.get("by") or ""}
        if not any(same_person(row, by) and str(row.get("role") or "").lower() in REASSIGN_ROLES for row in rows):
            continue
        replies = {}
        for who in (request.get("from") or "", request.get("to") or ""):
            try:
                replies[who] = json.loads((folder / _reply_name(request["id"], who)).read_text(encoding="utf-8"))
            except (OSError, ValueError, KeyError):
                pass
        request["replies"] = replies
        request["state"] = "imported" if (replies.get(request.get("to") or "") or {}).get("status") == "imported" else "pending"
        out.append(request)
    return out


def write_reply(cfg: dict, request: dict, status: str, extra: dict | None = None) -> None:
    who = me(cfg)["name"]
    reply = dict(extra or {}, status=status, by=who, at=datetime.now().astimezone().isoformat(timespec="seconds"))
    _write_atomic(_requests(cfg) / _reply_name(request["id"], who), json.dumps(reply, indent=2) + "\n")


def my_transfers(cfg: dict, roster: list[dict] | None = None) -> dict:
    who = me(cfg)
    incoming, outgoing = [], []
    for request in list_requests(cfg, roster):
        request["replied"] = any(same_person({"name": name}, who) for name in request.get("replies") or {})
        if same_person({"name": request.get("to")}, who):
            incoming.append(request)
        elif same_person({"name": request.get("from")}, who):
            outgoing.append(request)
    return {"incoming": incoming, "outgoing": outgoing}


def import_target(cfg: dict, request: dict) -> Path:
    rel = matcher.folder_name_for(
        request.get("customer") or "",
        list(request.get("so") or []),
        list(request.get("qte") or []),
        request.get("project_no") or "",
        matcher.folder_layout(cfg),
    )
    return Path(str(cfg.get("vault") or "")) / Path(*rel.split("/"))


def import_transfer(cfg: dict, request: dict) -> dict:
    """Copy the previous owner's folder into my vault. Never overwrites: refuses when the target exists."""
    ok, why = available(cfg)
    if not ok:
        raise OSError(why)
    if not same_person({"name": request.get("to")}, me(cfg)):
        raise PermissionError("this transfer is for %s" % request.get("to"))
    source = Path(str(request.get("from_folder") or ""))
    if not source.is_dir():
        raise FileNotFoundError("the previous owner's folder is not reachable: %s" % source)
    target = import_target(cfg, request)
    if target.exists():
        raise FileExistsError("%s already exists in your vault" % target)
    shutil.copytree(source, target)
    write_reply(cfg, request, "imported", {"folder": str(target)})
    return {"folder": str(target)}


# ---------------------------------------------------------------- mismatch flags

def owner_flags(cfg: dict, row: dict, schedule_people: list[dict] | None, schedule_mine: bool | None) -> list[str]:
    """Why this project may not be mine any more: the schedule or the APE sheet names someone else."""
    flags = []
    who = me(cfg)
    if schedule_mine is False:
        names = ", ".join(p.get("name") or "" for p in schedule_people or [] if p.get("name"))
        flags.append("Schedule does not list you%s" % (" (lists %s)" % names if names else ""))
    sheet_ae = str(((row.get("sheet") or {}).get("ae_name")) or "").strip()
    if sheet_ae and " " in sheet_ae and not same_person({"name": sheet_ae}, who):
        flags.append("APE sheet AE is %s" % sheet_ae)
    return flags
