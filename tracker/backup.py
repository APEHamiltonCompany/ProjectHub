"""Backup vault: two-way, record-based sync between the vault on R: (and extra folders such as TEMPLATES) and a
backup folder on an external or local drive, so the hub can keep working when R: is unreachable.

Each folder pair keeps a record (size + modified time of every file at the last sync) in data/backup. Comparing both
sides against that record tells which side changed:
- export (R: -> backup) copies files changed or new on R:, moves files deleted on R: into the backup's
  .projecthub_deleted folder, and leaves files changed in the backup alone (they wait for import);
- import (backup -> R:) is previewed first; it copies files changed or new in the backup, saves conflicts beside the
  R: file as "name (offline YYYY-MM-DD).ext", and deletes from R: only what you tick. Every R: file it overwrites or
  deletes is copied to backups/<stamp>_import first.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from . import notes

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "backup"
DELETED_DIR = ".projecthub_deleted"
SKIP_NAMES = {"thumbs.db", "desktop.ini"}
MTIME_SLACK = 2.0
REACH_TIMEOUT = 6.0


def settings(cfg: dict) -> dict:
    raw = cfg.get("backup_vault") or {}
    return raw if isinstance(raw, dict) else {}


def enabled(cfg: dict) -> bool:
    return bool(str(settings(cfg).get("path") or "").strip())


def online_vault(cfg: dict) -> str:
    return str(cfg.get("online_vault") or cfg.get("vault") or "")


def pairs(cfg: dict) -> list[dict]:
    """[{name, source (R:), backup}] for the vault and every extra folder. Empty when no backup path is set."""
    conf = settings(cfg)
    target = str(conf.get("path") or "").strip()
    if not target:
        return []
    out = []
    vault = online_vault(cfg)
    if vault:
        out.append({"name": "vault", "source": vault, "backup": target})
    for item in conf.get("folders") or []:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        backup = str(item.get("backup") or "").strip()
        if source and backup:
            out.append({"name": Path(source.replace("\\", "/")).name or source, "source": source, "backup": backup})
    return out


def reachable(path: str | Path, timeout: float = REACH_TIMEOUT) -> bool:
    """isdir() with a timeout: a dead network share can block for half a minute."""
    text = str(path or "")
    if not text:
        return False
    result: list[bool] = []
    worker = threading.Thread(target=lambda: result.append(os.path.isdir(text)), daemon=True)
    worker.start()
    worker.join(timeout)
    return bool(result and result[0])


def _norm(text: str) -> str:
    return os.path.normcase(os.path.normpath(str(text).replace("/", os.sep))).rstrip("\\/")


def swap_path(value: str, swaps: list[tuple[str, str]]) -> str:
    """Rewrite value when it lies under one of the (online, backup) roots; else return it unchanged."""
    if not isinstance(value, str) or not value:
        return value
    norm = _norm(value)
    for online, backup in swaps:
        root = _norm(online)
        if norm == root or norm.startswith(root + os.sep):
            rest = os.path.normpath(value.replace("/", os.sep))[len(root):].lstrip("\\/")
            return str(Path(backup) / rest) if rest else str(Path(backup))
    return value


def _swap_all(value, swaps):
    if isinstance(value, dict):
        return {key: (item if key in ("backup_vault", "online_vault") else _swap_all(item, swaps)) for key, item in value.items()}
    if isinstance(value, list):
        return [_swap_all(item, swaps) for item in value]
    return swap_path(value, swaps)


def apply_offline(cfg: dict, resolve=lambda text: text, check=None) -> dict:
    """Point the config at the backup when R: is unreachable (offline_fallback on) or when work_offline is on.
    resolve maps a path to the form this process can reach (mapped drive -> UNC). Adds cfg["offline"]."""
    conf = settings(cfg)
    found = pairs(cfg)
    vault_pair = found[0] if found and found[0]["name"] == "vault" else None
    info = {"active": False, "reason": "", "forced": bool(conf.get("work_offline")), "auto": bool(conf.get("offline_fallback", True))}
    if vault_pair is None:
        return dict(cfg, offline=info)
    check = check or reachable
    if info["forced"]:
        info.update(active=True, reason="Work offline is switched on")
    elif info["auto"] and not check(resolve(vault_pair["source"])):
        if check(resolve(vault_pair["backup"])):
            info.update(active=True, reason="%s is not reachable" % vault_pair["source"])
        else:
            info["reason"] = "%s and the backup %s are both unreachable" % (vault_pair["source"], vault_pair["backup"])
    if not info["active"]:
        return dict(cfg, offline=info)
    swaps = [(pair["source"], pair["backup"]) for pair in found]
    out = _swap_all(cfg, swaps)
    out["online_vault"] = vault_pair["source"]
    out["offline"] = info
    return out


# ---------------------------------------------------------------- scanning and records


def _skip(name: str) -> bool:
    low = name.lower()
    return low in SKIP_NAMES or low.startswith("~$") or low.endswith(".projecthub-tmp")


def scan(root: str | Path, errors: list[str] | None = None) -> dict[str, tuple[int, float]]:
    """{relative path with '/': (size, mtime)} for every file under root (skips Office lock files and our own folders).
    Folders that can't be listed are added to errors (relative paths) so nothing under them is treated as deleted."""
    base = notes.lp(root)
    out: dict[str, tuple[int, float]] = {}
    stack = [("", base)]
    while stack:
        rel, folder = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            if errors is not None:
                errors.append(rel)
            continue
        for entry in entries:
            name = entry.name
            child = rel + "/" + name if rel else name
            try:
                if entry.is_dir(follow_symlinks=False):
                    if not rel and name.lower().startswith(".projecthub"):
                        continue
                    stack.append((child, Path(entry.path)))
                elif entry.is_file(follow_symlinks=False) and not _skip(name):
                    info = entry.stat()
                    out[child] = (int(info.st_size), float(info.st_mtime))
            except OSError:
                continue
    return out


def same(a, b) -> bool:
    return bool(a and b) and int(a[0]) == int(b[0]) and abs(float(a[1]) - float(b[1])) <= MTIME_SLACK


def _record_path(pair: dict, data_dir: Path) -> Path:
    digest = hashlib.sha1(("%s|%s" % (_norm(pair["source"]), _norm(pair["backup"]))).encode("utf-8")).hexdigest()[:12]
    return data_dir / ("record_%s_%s.json" % (pair["name"].replace(" ", "_")[:30], digest))


def load_record(pair: dict, data_dir: Path = DATA_DIR) -> dict[str, list]:
    try:
        data = json.loads(_record_path(pair, data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    files = data.get("files") if isinstance(data, dict) else None
    return files if isinstance(files, dict) else {}


def load_old_copy(pair: dict, data_dir: Path = DATA_DIR) -> dict[str, list]:
    """Files that were only in the backup before the first export (an older manual copy): never ticked on import."""
    try:
        data = json.loads(_record_path(pair, data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    files = data.get("old_copy") if isinstance(data, dict) else None
    return files if isinstance(files, dict) else {}


def save_record(pair: dict, files: dict, data_dir: Path = DATA_DIR, old_copy: dict | None = None) -> None:
    data = {"source": pair["source"], "backup": pair["backup"], "files": files}
    if old_copy:
        data["old_copy"] = old_copy
    _write_json(_record_path(pair, data_dir), data)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(temp, path)
    except Exception:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def load_status(data_dir: Path = DATA_DIR) -> dict:
    try:
        data = json.loads((data_dir / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_status(status: dict, data_dir: Path = DATA_DIR) -> None:
    _write_json(data_dir / "status.json", status)


def update_status(changes: dict, data_dir: Path = DATA_DIR) -> dict:
    status = load_status(data_dir)
    status.update(changes)
    save_status(status, data_dir)
    return status


# ---------------------------------------------------------------- copying


def copy_file(source: Path, target: Path) -> None:
    """Copy with times kept; written to a temp name first so a failed copy never leaves half a file."""
    src = notes.lp(source)
    dst = notes.lp(target)
    dst.parent.mkdir(parents=True, exist_ok=True)
    temp = dst.with_name(dst.name + ".projecthub-tmp")
    try:
        shutil.copy2(src, temp)
        os.replace(temp, dst)
    except OSError:
        try:
            temp.unlink()
        except OSError:
            pass
        raise


def offline_name(rel: str, day: date) -> str:
    path = Path(rel)
    return str(path.with_name("%s (offline %s)%s" % (path.stem, day.isoformat(), path.suffix))).replace("\\", "/")


def _keep_old(root: Path, rel: str, store: Path) -> None:
    source = notes.lp(root / rel)
    if source.is_file():
        copy_file(root / rel, store / rel)


# ---------------------------------------------------------------- export


def plan_export(src: dict, dst: dict, record: dict) -> dict:
    """Decide what an export does. Returns {copy, delete, pending, conflict, only_backup, record}."""
    out = {"copy": [], "delete": [], "pending": [], "conflict": [], "only_backup": [], "record": {}}
    for rel in sorted(set(src) | set(dst)):
        s, d, b = src.get(rel), dst.get(rel), record.get(rel)
        if s and d and same(s, d):
            out["record"][rel] = list(s)
        elif s and not d:
            if b and same(b, s):
                out["pending"].append(rel)
                out["record"][rel] = b
            else:
                out["copy"].append(rel)
        elif d and not s:
            if b and same(b, d):
                out["delete"].append(rel)
            else:
                out["only_backup"].append(rel)
                if b:
                    out["record"][rel] = b
        else:
            if b:
                src_changed, dst_changed = not same(b, s), not same(b, d)
                if src_changed and dst_changed:
                    out["conflict"].append(rel)
                    out["record"][rel] = b
                elif dst_changed:
                    out["pending"].append(rel)
                    out["record"][rel] = b
                else:
                    out["copy"].append(rel)
            elif d[1] > s[1] + MTIME_SLACK:
                out["pending"].append(rel)
            else:
                out["copy"].append(rel)
    return out


def export_pair(pair: dict, data_dir: Path = DATA_DIR, progress=None, today: date | None = None) -> dict:
    source, backup = Path(pair["source"]), Path(pair["backup"])
    if not reachable(source):
        raise OSError("%s is not reachable" % source)
    notes.lp(backup).mkdir(parents=True, exist_ok=True)
    scan_errors: list[str] = []
    src, dst = scan(source, scan_errors), scan(backup)
    if not src and dst:
        raise OSError("%s looks empty; nothing was changed in the backup" % source)
    old = load_record(pair, data_dir)
    plan = plan_export(src, dst, old)
    if old:
        old_copy = {rel: v for rel, v in load_old_copy(pair, data_dir).items() if rel in dst and same(v, dst[rel])}
    else:
        old_copy = {rel: list(dst[rel]) for rel in plan["only_backup"]}
    record = plan["record"]
    unsafe = [rel for rel in plan["delete"] if any(rel == bad or rel.startswith(bad + "/") for bad in scan_errors if bad) or "" in scan_errors]
    held = []
    if unsafe or (len(plan["delete"]) > 50 and len(plan["delete"]) > len(old) // 4):
        held = list(plan["delete"])
        plan["delete"] = []
        for rel in held:
            record[rel] = list(dst[rel])
    copied, skipped, size = [], [], 0
    total = len(plan["copy"])
    for index, rel in enumerate(plan["copy"]):
        if progress:
            progress(pair["name"], index, total, rel)
        try:
            copy_file(source / rel, backup / rel)
        except OSError as exc:
            skipped.append("%s (%s)" % (rel, exc.strerror or exc))
            if rel in old:
                record[rel] = old[rel]
            continue
        copied.append(rel)
        size += src[rel][0]
        record[rel] = list(src[rel])
    moved = []
    stamp = (today or date.today()).isoformat()
    for rel in plan["delete"]:
        try:
            target = notes.lp(backup / DELETED_DIR / stamp / rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(notes.lp(backup / rel), target)
            moved.append(rel)
        except OSError as exc:
            skipped.append("%s (%s)" % (rel, exc.strerror or exc))
            record[rel] = list(dst[rel])
    save_record(pair, record, data_dir, old_copy)
    return {
        "name": pair["name"],
        "source": pair["source"],
        "backup": pair["backup"],
        "files": len(src),
        "bytes": sum(item[0] for item in src.values()),
        "copied": len(copied),
        "copied_bytes": size,
        "deleted": len(moved),
        "deletes_held": len(held),
        "scan_errors": scan_errors,
        "skipped": skipped,
        "pending": len(plan["pending"]),
        "conflicts": plan["conflict"],
        "only_backup": len(plan["only_backup"]),
    }


def export_all(cfg: dict, data_dir: Path = DATA_DIR, progress=None) -> dict:
    results, errors = [], []
    for pair in pairs(cfg):
        try:
            results.append(export_pair(pair, data_dir, progress))
        except OSError as exc:
            errors.append("%s: %s" % (pair["name"], exc))
    return {"at": datetime.now().astimezone().isoformat(timespec="seconds"), "pairs": results, "errors": errors}


# ---------------------------------------------------------------- import


def plan_import(src: dict, dst: dict, record: dict, old_copy: dict | None = None) -> list[dict]:
    """Items an import can apply: kind new / old / changed / conflict / newer / deleted. checked = ticked by default.
    old = only in the backup since before the first export (an older copy), so not ticked."""
    out = []
    old_copy = old_copy or {}
    for rel in sorted(set(src) | set(dst)):
        s, d, b = src.get(rel), dst.get(rel), record.get(rel)
        if s and d and same(s, d):
            continue
        if d and not s:
            if b and same(b, d):
                continue
            if rel in old_copy and same(old_copy[rel], d):
                out.append({"rel": rel, "kind": "old", "checked": False, "size": d[0], "mtime": d[1]})
            else:
                out.append({"rel": rel, "kind": "new", "checked": True, "size": d[0], "mtime": d[1]})
        elif s and not d:
            if b and same(b, s):
                out.append({"rel": rel, "kind": "deleted", "checked": False, "size": s[0], "mtime": s[1]})
        elif b:
            src_changed, dst_changed = not same(b, s), not same(b, d)
            if dst_changed and not src_changed:
                out.append({"rel": rel, "kind": "changed", "checked": True, "size": d[0], "mtime": d[1]})
            elif dst_changed and src_changed:
                out.append({"rel": rel, "kind": "conflict", "checked": True, "size": d[0], "mtime": d[1]})
        elif d[1] > s[1] + MTIME_SLACK:
            out.append({"rel": rel, "kind": "newer", "checked": False, "size": d[0], "mtime": d[1]})
    return out


def preview_import(cfg: dict, data_dir: Path = DATA_DIR) -> dict:
    out, errors = [], []
    for pair in pairs(cfg):
        if not reachable(pair["source"]):
            errors.append("%s: %s is not reachable" % (pair["name"], pair["source"]))
            continue
        if not reachable(pair["backup"]):
            errors.append("%s: backup %s is not reachable" % (pair["name"], pair["backup"]))
            continue
        items = plan_import(scan(pair["source"]), scan(pair["backup"]), load_record(pair, data_dir), load_old_copy(pair, data_dir))
        for item in items:
            item["pair"] = pair["name"]
            item["modified"] = datetime.fromtimestamp(item.pop("mtime")).strftime("%Y-%m-%d %H:%M")
        out.extend(items)
    return {"items": out, "errors": errors}


def apply_import(cfg: dict, picks: list[dict], backup_root: Path, data_dir: Path = DATA_DIR, today: date | None = None) -> dict:
    """picks: [{pair, rel}] from preview_import. Re-plans each pair so stale picks can't do the wrong thing."""
    day = today or date.today()
    store = Path(backup_root) / (datetime.now().strftime("%Y%m%d-%H%M%S") + "_import")
    wanted: dict[str, set[str]] = {}
    for pick in picks:
        wanted.setdefault(str(pick.get("pair") or ""), set()).add(str(pick.get("rel") or ""))
    done: list[str] = []
    failed: list[str] = []
    for pair in pairs(cfg):
        rels = wanted.get(pair["name"])
        if not rels:
            continue
        source, backup = Path(pair["source"]), Path(pair["backup"])
        if not reachable(source):
            failed.append("%s: %s is not reachable" % (pair["name"], source))
            continue
        src, dst = scan(source), scan(backup)
        record = load_record(pair, data_dir)
        old_copy = load_old_copy(pair, data_dir)
        for item in plan_import(src, dst, record, old_copy):
            rel = item["rel"]
            if rel not in rels:
                continue
            try:
                if item["kind"] in ("new", "old", "changed", "newer"):
                    _keep_old(source, rel, store)
                    copy_file(backup / rel, source / rel)
                    record[rel] = list(dst[rel])
                elif item["kind"] == "conflict":
                    target = offline_name(rel, day)
                    copy_file(backup / rel, source / target)
                    copy_file(backup / rel, backup / target)
                    copy_file(source / rel, backup / rel)
                    record[target] = list(dst[rel])
                    record[rel] = list(src[rel])
                elif item["kind"] == "deleted":
                    _keep_old(source, rel, store)
                    notes.lp(source / rel).unlink()
                    record.pop(rel, None)
            except OSError as exc:
                failed.append("%s (%s)" % (rel, exc.strerror or exc))
                continue
            done.append("%s: %s" % (item["kind"], rel))
        save_record(pair, record, data_dir, {rel: v for rel, v in old_copy.items() if rel not in record})
    return {"applied": done, "failed": failed, "kept": str(store) if notes.lp(store).exists() else ""}


# ---------------------------------------------------------------- schedule


def parse_time(text) -> tuple[int, int] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        hour, minute = raw.split(":", 1)
        hour_i, minute_i = int(hour), int(minute)
    except ValueError:
        return None
    if 0 <= hour_i < 24 and 0 <= minute_i < 60:
        return hour_i, minute_i
    return None


def last_slot(now: datetime, daily: tuple[int, int]) -> datetime:
    slot = now.replace(hour=daily[0], minute=daily[1], second=0, microsecond=0)
    return slot if slot <= now else slot - timedelta(days=1)


def next_slot(now: datetime, daily: tuple[int, int]) -> datetime:
    return last_slot(now, daily) + timedelta(days=1)


def export_due(cfg: dict, status: dict, now: datetime) -> bool:
    """True when the daily export time has passed since the last export (catches runs missed while the PC was off)."""
    daily = parse_time(settings(cfg).get("daily_time"))
    if daily is None or not enabled(cfg) or (cfg.get("offline") or {}).get("active"):
        return False
    last = status.get("last_export_at") or ""
    try:
        when = datetime.fromisoformat(last)
    except ValueError:
        return True
    if when.tzinfo is not None:
        when = when.astimezone().replace(tzinfo=None)
    return when < last_slot(now.replace(tzinfo=None), daily)
