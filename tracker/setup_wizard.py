from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from tracker import backup as backup_mod
from tracker import settings as settings_mod

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EXAMPLE = HERE / "config.example.yaml"
DEFAULT_CONFIG = HERE / "config.yaml"
OVERLAY_KEYS = ("vault", "personal_tasks_path", "time_estimate_template")


def initials_for(name: str) -> str:
    words = [word for word in str(name or "").replace(",", " ").split() if word]
    if not words:
        return ""
    if len(words) == 1:
        return words[0][:1].upper()
    return (words[0][:1] + words[-1][:1]).upper()


def default_vault(onedrive: str | Path) -> str:
    base = Path(onedrive)
    candidates = [
        base / "Desktop" / "Projects" / "Project Specific Documents",
        base / "Projects" / "Project Specific Documents",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return str(candidate)
    return str(candidates[-1])


def _fwd(value) -> str:
    return str(value or "").replace("\\", "/")


def _git(key: str) -> str:
    try:
        result = subprocess.run(
            ["git", "config", key], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _outlook_user():
    try:
        import win32com.client

        return win32com.client.Dispatch("Outlook.Application").Session.CurrentUser
    except Exception:
        return None


def detect_defaults() -> dict:
    user = _outlook_user()
    name = ""
    email = ""
    if user is not None:
        try:
            name = str(user.Name or "").strip()
        except Exception:
            name = ""
        try:
            email = str(user.AddressEntry.GetExchangeUser().PrimarySmtpAddress or "").strip()
        except Exception:
            email = ""
    name = name or _git("user.name") or os.environ.get("USERNAME") or ""
    email = email or _git("user.email") or ""
    onedrive = (
        os.environ.get("OneDriveCommercial")
        or os.environ.get("OneDrive")
        or str(Path.home())
    )
    vault = default_vault(onedrive)
    template = Path(vault).parent / "templates" / "TimeEstimates.xlsx"
    return {
        "name": name,
        "email": email,
        "initials": initials_for(name),
        "vault": vault,
        "personal_tasks_path": str(Path(vault) / "My Tasks.md"),
        "time_estimate_template": str(template) if template.is_file() else "",
        "extra_eml_folder": "",
        "wire_diagram_builder": "",
        "port": 8765,
    }


def build_config(base: dict, answers: dict) -> dict:
    cfg = dict(base or {})
    cfg["vault"] = _fwd(answers.get("vault"))
    cfg["personal_tasks_path"] = _fwd(answers.get("personal_tasks_path"))
    cfg["time_estimate_template"] = _fwd(answers.get("time_estimate_template"))
    me = dict(cfg.get("me") or {})
    me["name"] = str(answers.get("name") or "")
    me["email"] = str(answers.get("email") or "")
    cfg["me"] = me
    sheet = dict(cfg.get("ape_sheet") or {})
    sheet["my_initials"] = str(answers.get("initials") or "")
    cfg["ape_sheet"] = sheet
    folders = ["email_drop"]
    extra = _fwd(answers.get("extra_eml_folder")).strip()
    if extra:
        folders.append(extra)
    cfg["eml_folders"] = folders
    tools = dict(cfg.get("tools") or {})
    tools["wire_diagram_builder"] = _fwd(answers.get("wire_diagram_builder"))
    cfg["tools"] = tools
    hub = dict(cfg.get("hub") or {})
    hub["port"] = int(answers.get("port") or 8765)
    cfg["hub"] = hub
    if "backup_path" in answers:
        vault_backup = dict(cfg.get("backup_vault") or {})
        vault_backup["path"] = _fwd(answers.get("backup_path"))
        vault_backup["daily_time"] = str(answers.get("backup_daily_time") or "")
        vault_backup.setdefault("offline_fallback", True)
        vault_backup.setdefault("folders", [])
        cfg["backup_vault"] = vault_backup
    return cfg


def load_base(target: Path) -> dict:
    base: dict = {}
    if EXAMPLE.is_file():
        base = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8")) or {}
    if target.is_file():
        existing = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        if isinstance(existing, dict):
            base = {**base, **existing}
    return base if isinstance(base, dict) else {}


def stored_answers(base: dict) -> dict:
    return {
        "name": (base.get("me") or {}).get("name") or "",
        "email": (base.get("me") or {}).get("email") or "",
        "initials": (base.get("ape_sheet") or {}).get("my_initials") or "",
        "vault": base.get("vault") or "",
        "personal_tasks_path": base.get("personal_tasks_path") or "",
        "time_estimate_template": base.get("time_estimate_template") or "",
        "wire_diagram_builder": (base.get("tools") or {}).get("wire_diagram_builder") or "",
        "port": (base.get("hub") or {}).get("port") or "",
        "backup_path": (base.get("backup_vault") or {}).get("path") or "",
        "backup_daily_time": (base.get("backup_vault") or {}).get("daily_time") or "",
    }


def _ask(ask, label: str, default) -> str:
    text = str(default or "")
    prompt = "%s [%s]: " % (label, text) if text else "%s: " % label
    reply = str(ask(prompt) or "").strip()
    return reply or text


def _yes(ask, label: str) -> bool:
    return str(ask(label) or "").strip().lower() not in ("n", "no")


def prompt_answers(defaults: dict, ask=input) -> dict:
    answers = dict(defaults)
    answers["name"] = _ask(ask, "Your name (as it appears in PM schedules)", answers.get("name"))
    answers["email"] = _ask(ask, "Work email", answers.get("email"))
    answers["initials"] = _ask(
        ask, "Initials used in the APE tracking sheet", answers.get("initials")
    )
    old_vault = str(answers.get("vault") or "")
    vault = _ask(ask, "Project folder root (vault)", old_vault)
    if vault and not Path(vault).is_dir() and _yes(ask, "Create it? [Y/n] "):
        Path(vault).mkdir(parents=True, exist_ok=True)
    answers["vault"] = vault
    tasks_default = str(answers.get("personal_tasks_path") or "")
    if not tasks_default or tasks_default == str(Path(old_vault) / "My Tasks.md"):
        tasks_default = str(Path(vault) / "My Tasks.md")
    answers["personal_tasks_path"] = _ask(ask, "My Tasks file", tasks_default)
    template = _ask(
        ask,
        "Time estimate template .xlsx (optional, blank to skip)",
        answers.get("time_estimate_template"),
    )
    while template and not Path(template).is_file():
        print("  not found: %s" % template)
        template = _ask(ask, "Time estimate template .xlsx (optional, blank to skip)", "")
    answers["time_estimate_template"] = template
    answers["extra_eml_folder"] = _ask(
        ask, "Extra email drop folder (optional)", answers.get("extra_eml_folder")
    )
    tool = _ask(
        ask, "WireDiagramBuilder.exe path (optional)", answers.get("wire_diagram_builder")
    )
    while tool and not Path(tool).is_file():
        print("  not found: %s" % tool)
        tool = _ask(ask, "WireDiagramBuilder.exe path (optional)", "")
    answers["wire_diagram_builder"] = tool
    port = _ask(ask, "Hub port", answers.get("port") or 8765)
    while True:
        try:
            number = int(str(port).strip())
        except ValueError:
            number = 0
        if 1024 <= number <= 65535:
            break
        print("  port must be a number between 1024 and 65535")
        port = _ask(ask, "Hub port", 8765)
    answers["port"] = number
    backup_path = _ask(
        ask, "Backup vault folder, e.g. an external drive (optional, blank to skip)", answers.get("backup_path")
    )
    answers["backup_path"] = backup_path
    if backup_path:
        daily = _ask(ask, "Daily backup time HH:MM (blank = only by hand)", answers.get("backup_daily_time") or "14:00")
        while daily and backup_mod.parse_time(daily) is None:
            print("  time must look like 14:00")
            daily = _ask(ask, "Daily backup time HH:MM (blank = only by hand)", "14:00")
        answers["backup_daily_time"] = daily
    else:
        answers["backup_daily_time"] = ""
    return answers


def clean_settings(config_path: Path, answers: dict) -> list[str]:
    path = settings_mod.settings_path_for(config_path)
    overlay = settings_mod.load_overlay(path)
    if not overlay:
        return []
    removed = []
    for key in OVERLAY_KEYS:
        value = overlay.get(key)
        if isinstance(value, str) and value.strip() and _fwd(value.strip()) != _fwd(answers.get(key)):
            overlay.pop(key)
            removed.append(key)
    if removed:
        settings_mod.save_overlay(path, overlay)
    return removed


def write_config(target: Path, cfg: dict) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


def ensure_files(cfg: dict) -> None:
    raw = str(cfg.get("personal_tasks_path") or "").strip()
    if raw:
        tasks = Path(raw)
        if not tasks.is_file():
            tasks.parent.mkdir(parents=True, exist_ok=True)
            tasks.write_text("**My tasks**\n\n", encoding="utf-8")
    (ROOT / "email_drop").mkdir(parents=True, exist_ok=True)


def dry_run(config_path: Path) -> int:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "tracker.tracker", "--dry-run", "--config", str(config_path)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print("dry run could not start: %s" % exc)
        return 1
    text = (result.stdout or "") + (result.stderr or "")
    print("\n".join(text.strip().splitlines()[-25:]))
    if result.returncode != 0:
        print("WARNING: dry run exited with %d" % result.returncode)
    return result.returncode


def main(argv: list[str] | None = None, ask=input, defaults: dict | None = None) -> int:
    parser = argparse.ArgumentParser(prog="setup_wizard", description="Configure ProjectHub")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="path to config.yaml")
    parser.add_argument("--defaults", action="store_true", help="accept all defaults")
    args = parser.parse_args(argv)

    target = Path(args.config)
    base = load_base(target)
    detected = dict(defaults if defaults is not None else detect_defaults())
    stored = {key: value for key, value in stored_answers(base).items() if value}
    merged = {**detected, **stored}
    if not merged.get("extra_eml_folder"):
        merged["extra_eml_folder"] = next(
            (
                str(folder)
                for folder in (base.get("eml_folders") or [])
                if Path(str(folder)).name != "email_drop"
            ),
            "",
        )

    answers = merged if args.defaults else prompt_answers(merged, ask)
    cfg = build_config(base, answers)
    if target.is_file():
        shutil.copy2(target, Path(str(target) + ".bak"))
    write_config(target, cfg)
    ensure_files(cfg)
    removed = clean_settings(target, answers)

    print("")
    print("config written: %s" % target)
    print("  name:     %s" % cfg["me"]["name"])
    print("  email:    %s" % cfg["me"]["email"])
    print("  initials: %s" % cfg["ape_sheet"]["my_initials"])
    print("  vault:    %s" % cfg["vault"])
    print("  tasks:    %s" % cfg["personal_tasks_path"])
    print("  hub port: %s" % cfg["hub"]["port"])
    if removed:
        print("  settings.json overrides cleared: %s" % ", ".join(removed))

    if not args.defaults and _yes(ask, "Run a dry-run check now? [Y/n] "):
        dry_run(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
