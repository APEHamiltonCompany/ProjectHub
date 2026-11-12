from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

TOP_KEYS = ("vault", "personal_tasks_path", "time_estimate_template", "ape_sheet_path")
HUB_KEYS = ("refresh_minutes", "due_soon_days", "theme", "auto_reload_pages")
THEMES = ("system", "light", "dark")


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


def apply_overlay(cfg: dict, overlay: dict) -> dict:
    merged = dict(cfg or {})
    hub = dict(merged.get("hub") or {})
    for key in TOP_KEYS:
        value = overlay.get(key)
        if isinstance(value, str) and value.strip():
            merged[key] = value.strip()
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
