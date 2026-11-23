from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

TOP_KEYS = ("vault", "personal_tasks_path", "time_estimate_template", "ape_sheet_path")
HUB_KEYS = ("refresh_minutes", "due_soon_days", "theme", "auto_reload_pages")
THEMES = ("system", "light", "dark")
BACKUP_KEYS = ("work_offline", "offline_fallback", "daily_time")


def settings_path_for(config_path: str | Path) -> Path:
    return Path(config_path).resolve().parent / "settings.json"


def load_overlay(path: str | Path) -> dict:
    target = Path(path)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_overlay(path: str | Path, data: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=str(target.parent), prefix=".settings-", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(temp, target)
    except Exception:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def unc_path(text: str) -> str:
    """'R:/x' -> '\\\\server\\share\\x' when R: is a mapped network drive that this process can't see
    (scheduled tasks and background processes often start before mapped drives reconnect). Else ''."""
    if os.name != "nt" or len(text) < 3 or text[1] != ":" or text[2] not in "\\/" or not text[0].isalpha():
        return ""
    if os.path.exists(text[:3]):
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Network\\" + text[0].upper()) as key:
            remote = str(winreg.QueryValueEx(key, "RemotePath")[0] or "").strip().rstrip("\\/")
    except OSError:
        return ""
    if not remote:
        return ""
    return remote + "\\" + text[3:].replace("/", "\\")


def resolve_drives(value):
    """Swap disconnected mapped-drive paths in a loaded config for their UNC form (in memory only)."""
    if isinstance(value, dict):
        return {key: resolve_drives(item) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_drives(item) for item in value]
    if isinstance(value, str):
        return unc_path(value) or value
    return value


def go_offline(cfg: dict) -> dict:
    """Swap R: paths for the backup vault when R: is unreachable or Work offline is on (see tracker/backup.py)."""
    from tracker import backup

    return backup.apply_offline(cfg, resolve=lambda text: unc_path(text) or text)


def apply_overlay(cfg: dict, overlay: dict) -> dict:
    merged = dict(cfg or {})
    saved = overlay.get("backup")
    if isinstance(saved, dict):
        conf = dict(merged.get("backup_vault") or {})
        for key in ("work_offline", "offline_fallback"):
            if isinstance(saved.get(key), bool):
                conf[key] = saved[key]
        if isinstance(saved.get("daily_time"), str):
            conf["daily_time"] = saved["daily_time"].strip()
        merged["backup_vault"] = conf
    hub = dict(merged.get("hub") or {})
    for key in TOP_KEYS:
        value = overlay.get(key)
        if isinstance(value, str) and value.strip():
            merged[key] = value.strip()
    checklist = overlay.get("checklist_optional")
    if isinstance(checklist, list):
        merged["checklist_optional"] = list(dict.fromkeys(str(value) for value in checklist))
    for key in HUB_KEYS:
        if key not in overlay:
            continue
        value = overlay[key]
        if key in ("refresh_minutes", "due_soon_days"):
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                hub[key] = value
        elif key == "theme":
            if value in THEMES:
                hub[key] = value
        elif key == "auto_reload_pages":
            if isinstance(value, bool):
                hub[key] = value
    merged["hub"] = hub
    return merged
