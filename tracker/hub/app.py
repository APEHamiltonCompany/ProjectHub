from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import yaml
from flask import Flask, abort, g, jsonify, render_template, request, url_for
from markupsafe import Markup, escape

from tracker import locking, matcher, notes
from tracker import project_files
from tracker import projects as projects_mod
from tracker import rules as rules_mod
from tracker import settings as settings_mod
from tracker import state as state_mod
from tracker.hub import tasks as tasks_mod
from tracker.hub import timesheet as timesheet_mod
from tracker.hub import winfocus
from tracker.sources import ape_sheet, outlook_com

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CHECKBOX_RE = re.compile(r"^(\s*[-*]\s+\[)([ xX-])(\]\s.*)$")
NOTE_BAD_RE = re.compile(r'[<>:"/\\|?*]')
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
URL_RE = re.compile(r"https?://[^\s<>\"')]+")
ID_RE = re.compile(notes.ID_EMOJI + r"\s*([A-Za-z0-9_-]+)")
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
CLASSIFY_CHOICES = [
    ("kickoff", "Kickoff"),
    ("design_review", "Design review"),
    ("internal_design_review", "Internal DR"),
    ("work_order_firm", "WO firm"),
    ("closeout", "Closeout"),
    ("ignore", "Ignore"),
]
CHECK_GLYPHS = {" ": "\u2610", "x": "\u2611", "-": "\u2298"}
NEXT_MARK = {" ": "x", "x": "-", "-": " "}
LAYOUTS = ("board", "timeline", "focus")
ZOOMS = (100, 90, 80, 75, 70, 60, 50)
ORIGIN_FILE = ".projecthub_origin"
COMPLETION_STAGE = "Completion"
DEFAULT_STAGES = ["Pre-Kickoff", "Pre-Fabrication", "System Test", "Closeout", "Completion"]


def _load_config(path: str | Path) -> dict:
    if not Path(path).is_file():
        raise SystemExit("Config not found: %s\nRun setup.cmd to create it." % path)
    with open(path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    cfg = settings_mod.apply_overlay(cfg, settings_mod.load_overlay(settings_mod.settings_path_for(path)))
    return settings_mod.resolve_drives(cfg)


def _ui_prefs(raw: dict) -> dict:
    layout = str(raw.get("layout") or "")
    try:
        zoom = int(raw.get("zoom"))
    except (TypeError, ValueError):
        zoom = 100
    fit = raw.get("fit")
    return {
        "layout": layout if layout in LAYOUTS else "board",
        "zoom": zoom if zoom in ZOOMS else 100,
        "fit": True if fit is None else bool(fit),
    }


def _ui_update(payload: dict) -> dict:
    update: dict = {}
    if "layout" in payload:
        layout = str(payload.get("layout") or "")
        if layout not in LAYOUTS:
            raise ValueError("layout")
        update["layout"] = layout
    if "zoom" in payload:
        raw = payload.get("zoom")
        if isinstance(raw, bool):
            raise ValueError("zoom")
        try:
            zoom = int(raw)
        except (TypeError, ValueError):
            raise ValueError("zoom")
        if zoom not in ZOOMS:
            raise ValueError("zoom")
        update["zoom"] = zoom
    if "fit" in payload:
        fit = payload.get("fit")
        if not isinstance(fit, bool):
            raise ValueError("fit")
        update["fit"] = fit
    if not update:
        raise ValueError("ui")
    return update


def _clean_path(value) -> str:
    return str(value or "").strip().strip('"').strip("'").strip().replace("\\", "/")


def project_ident(project: dict) -> str:
    """SO/QTE shown for a project: "SO-285770 (QTE-054686)", "QTE-054686", else the project number or name."""
    so = ", ".join(project.get("so") or [])
    qte = ", ".join(project.get("qte") or [])
    if so and qte:
        return "%s (%s)" % (so, qte)
    return so or qte or str(project.get("project_no") or "") or str(project.get("name") or project.get("key") or "")


def project_label(project: dict) -> str:
    """One-line display name: "Customer · SO/QTE"."""
    customer = str(project.get("customer") or "").strip()
    ident = project_ident(project)
    return "%s \u00b7 %s" % (customer, ident) if customer and ident else customer or ident


def _iso(value) -> date | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _time_payload(row: dict) -> dict:
    entries = [
        {"day": when.isoformat(), "hours": hours} for when, hours in row.get("time") or []
    ]
    return {
        "ok": True,
        "line": row.get("line") or "",
        "time": entries,
        "tracked": round(sum(item["hours"] for item in entries), 2),
    }


def _is_http(url: str) -> bool:
    return urlparse(str(url or "")).scheme in ("http", "https")


def _launch_keys(task_id: str) -> tuple[str, ...]:
    if task_id.startswith("p") and "-" in task_id:
        return (task_id, task_id.split("-", 1)[1])
    return (task_id,)


def _launch_label(target: str) -> str:
    text = str(target or "")
    if not text:
        return ""
    if _is_http(text):
        return urlparse(text).netloc or text
    path = Path(text)
    return path.stem if path.suffix else path.name


def _template_label(name: str, spec: dict) -> str:
    label = str(spec.get("label") or "").strip()
    if label:
        return label
    pattern = str(spec.get("name") or "").strip() or str(name or "")
    stem = Path(pattern).stem if Path(pattern).suffix else pattern
    text = re.sub(r"\{[^}]*\}", "", stem)
    text = re.sub(r"\s+", " ", text).strip(" -_.")
    return text or str(name or "")


def _template_title(spec: dict) -> str:
    return "%s \u2192 %s" % (str(spec.get("source") or ""), str(spec.get("name") or ""))


def _ref_buckets(ref: str) -> tuple[list[str], list[str]]:
    text = str(ref or "").strip()
    if text.upper().startswith("SO-"):
        return [text], []
    if text.upper().startswith("QTE-"):
        return [], [text]
    return [], []


def apply_fill(path: str | Path, fill: dict, tokens: dict) -> None:
    return None


def _inline(text: str) -> str:
    html = str(escape(text))
    html = BOLD_RE.sub(r"<strong>\1</strong>", html)
    html = URL_RE.sub(
        lambda m: '<a href="%s" target="_blank" rel="noopener">%s</a>' % (m.group(0), m.group(0)),
        html,
    )
    return html


def _task_title(line: str) -> str:
    match = CHECKBOX_RE.match(line)
    text = match.group(3)[1:].strip() if match else line.strip()
    text = notes.DUE_RE.sub("", text)
    text = notes.DONE_RE.sub("", text)
    text = ID_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _key_for_folder(folder: str) -> str:
    name = Path(folder).name
    keys = matcher.extract_keys(name)
    for bucket in ("project_no", "so", "qte"):
        if keys[bucket]:
            return sorted(keys[bucket])[0]
    return name


def _toast_script() -> str:
    return (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
        "ContentType = WindowsRuntime] | Out-Null\n"
        "$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02)\n"
        "$texts = $template.GetElementsByTagName('text')\n"
        "$texts.Item(0).AppendChild($template.CreateTextNode('Project Hub')) | Out-Null\n"
        "$texts.Item(1).AppendChild($template.CreateTextNode($env:HUB_TOAST_TEXT)) | Out-Null\n"
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($template)\n"
        "$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe'\n"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show($toast)\n"
    )


def email_opener(kind: str, target: str) -> None:
    if kind == "outlook":
        outlook_com.display_item(target)
        return
    os.startfile(target)


def launcher(target: str) -> None:
    text = str(target or "")
    if _is_http(text):
        webbrowser.open(text)
        return
    path = Path(text)
    before = winfocus.top_windows()
    if path.suffix.lower() in (".exe", ".bat", ".cmd"):
        proc = subprocess.Popen(
            [text],
            cwd=str(path.parent),
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        )
        winfocus.raise_after_launch(before, proc.pid)
        return
    os.startfile(text)
    winfocus.raise_after_launch(before)


def create_app(
    config_path: str | Path,
    port: int | None = None,
    no_refresh: bool = False,
    runner=None,
) -> Flask:
    cfg = _load_config(config_path)
    cfg_path = str(Path(config_path).resolve())
    settings_path = settings_mod.settings_path_for(config_path)
    hub_cfg = cfg.get("hub") or {}
    port = int(port or hub_cfg.get("port") or 8765)
    toast_enabled = bool(hub_cfg.get("toast", True))
    token = secrets.token_urlsafe(32)
    state_path = Path(cfg.get("state_path") or (ROOT / "tracker" / "state.json"))
    snapshot_path = Path(cfg.get("snapshot_path") or (ROOT / "output" / "snapshot.json"))
    templates_dir = Path(cfg.get("templates_dir") or (ROOT / "templates"))
    live: dict = {}

    def apply_settings() -> None:
        hub = cfg.get("hub") or {}
        vault = Path(cfg.get("vault") or "")
        minutes = hub.get("refresh_minutes")
        soon = hub.get("due_soon_days")
        theme = str(hub.get("theme") or "system")
        live["vault"] = vault
        live["personal_path"] = Path(cfg.get("personal_tasks_path") or (vault / "My Tasks.md"))
        live["refresh_minutes"] = 15 if minutes is None else int(minutes)
        live["due_soon_days"] = 7 if soon is None else int(soon)
        live["theme"] = theme if theme in settings_mod.THEMES else "system"
        live["auto_reload_pages"] = bool(hub.get("auto_reload_pages", True))

    apply_settings()
    lock_file = locking.lock_path_for(cfg)
    run_log = ROOT / "output" / "hub_last_run.log"
    rules_path = Path(cfg.get("rules_path") or (ROOT / "tracker" / "rules.yaml"))
    rules = rules_mod.load_rules(rules_path, cfg) if rules_path.is_file() else {"triggers": [], "workflow": [], "stages": [], "buckets": []}
    stage_order = list(rules.get("stages") or []) + [notes.PM_HEADING]
    lifecycle: list[str] = rules_mod.bucket_order(rules)
    if lifecycle and "System Test" not in lifecycle:
        position = lifecycle.index("Closeout") if "Closeout" in lifecycle else len(lifecycle)
        lifecycle.insert(position, "System Test")
    if lifecycle and "Completion" not in lifecycle:
        lifecycle.append("Completion")
    trigger_ids = {trigger.get("id") for trigger in rules.get("triggers") or []}
    template_launch = {
        str(step["id"]): str(step.get("launch") or "")
        for step in rules.get("workflow") or []
        if step.get("id")
    }
    classify_choices = [
        (str(t.get("id")), str(t.get("label") or dict(CLASSIFY_CHOICES).get(t.get("id")) or t.get("id")))
        for t in rules.get("triggers") or []
        if t.get("id") != "assignment"
    ] + [("ignore", "Ignore")]

    allowed_hosts = {"127.0.0.1:%d" % port, "localhost:%d" % port}
    allowed_origins = {"http://127.0.0.1:%d" % port, "http://localhost:%d" % port}

    mutex = threading.Lock()
    run_state = {"running": False, "queued": False, "last_run": "", "last_exit": None}
    seen_pending: set[str] = set()

    app = Flask(__name__)
    app.config["HUB_TOKEN"] = token
    app.config["HUB_PORT"] = port
    app.config["EMAIL_OPENER"] = email_opener
    app.config["LAUNCHER"] = launcher
    app.jinja_env.globals["categories"] = tasks_mod.CATEGORIES
    app.jinja_env.globals["weekdays"] = tasks_mod.WEEKDAYS

    def load_snapshot() -> dict:
        data = {"generated": "", "today": date.today().isoformat(), "projects": [], "pending": [], "unmatched": []}
        try:
            loaded = json.loads(snapshot_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data.update(loaded)
        except (OSError, ValueError):
            pass
        return data

    def snapshot() -> dict:
        try:
            cached = getattr(g, "_hub_snapshot", None)
        except RuntimeError:
            cached = None
        if cached is not None:
            return cached
        data = load_snapshot()
        g._hub_snapshot = data
        return data

    def project_by_key(key: str) -> dict | None:
        for project in snapshot().get("projects") or []:
            if project.get("key") == key:
                return project
        return None

    def task_note_root() -> Path:
        vault = live.get("vault")
        if vault:
            return Path(vault)
        return Path(live["personal_path"]).parent

    def task_note_rel(path: Path) -> str:
        try:
            return path.relative_to(task_note_root()).as_posix()
        except ValueError:
            return path.as_posix()

    def task_note_slug(title: str) -> str:
        text = NOTE_BAD_RE.sub(" ", str(title or ""))
        text = re.sub(r"\s+", " ", text).strip().strip(".")
        return text[:80].strip() or "Task"

    def task_note_target(task_id: str, title: str) -> Path:
        base = task_note_root() / "My Tasks"
        slug = task_note_slug(title)
        folder = base / slug
        if notes.lp(folder).is_dir():
            existing = folder / (slug + ".md")
            if notes.lp(existing).is_file():
                data, _raw, _body = notes.split_frontmatter(notes.read_text(existing)[0])
                if str(data.get("task_id") or "") not in ("", task_id):
                    folder = base / ("%s (%s)" % (slug, task_id))
        return folder / (folder.name + ".md")

    def personal_task_row(task_id: str) -> dict | None:
        path = live["personal_path"]
        if not notes.lp(path).is_file():
            return None
        text, _nl, _bom, _enc = notes.read_text(path)
        for row in tasks_mod.parse_lines(text):
            if row["id"] == task_id:
                return row
        return None

    def task_note_path(task_id: str) -> Path | None:
        row = personal_task_row(task_id)
        if row is None:
            return None
        rel = row.get("note") or ""
        if not rel:
            return None
        path = Path(rel)
        if not path.is_absolute():
            path = task_note_root() / path
        return path if notes.lp(path).is_file() else None

    def task_note_frontmatter(task_id: str) -> dict:
        path = task_note_path(task_id)
        if path is None:
            return {}
        data, _raw, _body = notes.split_frontmatter(notes.read_text(path)[0])
        return data

    def ensure_task_note(task_id: str, title: str) -> Path:
        path = task_note_path(task_id)
        if path is not None:
            return path
        path = task_note_target(task_id, title)
        if not notes.lp(path).is_file():
            notes.lp(path).parent.mkdir(parents=True, exist_ok=True)
            data = {"task_id": task_id, "folder": "", "launch": "", "links": []}
            notes.write_text(path, notes.dump_frontmatter(data) + "## Notes\n", "\n", False, "utf-8")
        rel = task_note_rel(path)
        source = live["personal_path"]
        with mutex:
            with locking.file_lock(lock_file):
                text, newline, bom, encoding = notes.read_text(source)
                try:
                    new_text = tasks_mod.update_manual(text, task_id, note=rel)
                except KeyError:
                    return path
                if new_text != text:
                    notes.write_text(source, new_text, newline, bom, encoding)
        return path

    def task_note_for(task_id: str) -> Path | None:
        row = personal_task_row(task_id)
        if row is None:
            return None
        return ensure_task_note(task_id, row.get("title") or task_id)

    def task_note_links(data: dict) -> list[dict]:
        rows = []
        for link in data.get("links") or []:
            if isinstance(link, dict):
                url = str(link.get("url") or "").strip()
                title = str(link.get("title") or "").strip() or url
            else:
                url = str(link or "").strip()
                title = url
            if url:
                rows.append({"title": title, "url": url, "http": _is_http(url)})
        return rows

    def task_note_folder(data: dict, path: Path) -> Path:
        raw = str(data.get("folder") or "").strip()
        if raw:
            folder = Path(raw)
            if not folder.is_absolute():
                folder = task_note_root() / folder
            if notes.lp(folder).is_dir():
                return folder
        return path.parent

    def folder_files(folder: str | Path) -> list[dict]:
        rows = []
        if not str(folder or ""):
            return rows
        try:
            for entry in sorted(notes.lp(folder).iterdir(), key=lambda p: p.name.lower()):
                if entry.name.startswith("~$") or not notes.lp(entry).is_file():
                    continue
                rows.append(
                    {
                        "name": entry.name,
                        "modified": datetime.fromtimestamp(notes.lp(entry).stat().st_mtime).strftime(
                            "%Y-%m-%d %H:%M"
                        ),
                    }
                )
        except OSError:
            pass
        return rows

    def resolve_launch(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if text.startswith("tool:"):
            text = str((cfg.get("tools") or {}).get(text[5:].strip()) or "").strip()
            if not text:
                return ""
        elif text.startswith("file:"):
            name = text[5:].strip()
            if project_files.item_by_id(cfg, name) or project_files.group_by_id(cfg, name):
                return "file:%s" % name
            return resolve_launch("template:%s" % name)
        elif text.startswith("template:"):
            name = text[9:].strip()
            spec = (cfg.get("file_templates") or {}).get(name)
            if not isinstance(spec, dict) or not str(spec.get("source") or "").strip():
                return ""
            return "template:%s" % name
        if _is_http(text):
            return text
        return os.path.expandvars(text)

    def launch_label_for(target: str) -> str:
        text = str(target or "")
        if text.startswith("file:"):
            return project_file_label(text[5:].strip())
        if not text.startswith("template:"):
            return _launch_label(text)
        name = text[9:].strip()
        spec = (cfg.get("file_templates") or {}).get(name)
        return _template_label(name, spec if isinstance(spec, dict) else {})

    def launch_meta(target: str) -> dict:
        text = str(target or "")
        if text.startswith("file:"):
            return {"launch_kind": "template", "launch_title": "Open the project's %s (copied from the template if missing)" % project_file_label(text[5:].strip())}
        if not text.startswith("template:"):
            return {"launch_kind": "open", "launch_title": text}
        name = text[9:].strip()
        spec = (cfg.get("file_templates") or {}).get(name)
        return {
            "launch_kind": "template",
            "launch_title": _template_title(spec if isinstance(spec, dict) else {}),
        }

    def launch_target(key: str, task_id: str, frontmatter: dict | None = None) -> str:
        if not task_id:
            return ""
        if not key and task_id.startswith(tasks_mod.MANUAL_PREFIX):
            if frontmatter is None:
                frontmatter = task_note_frontmatter(task_id)
            return resolve_launch((frontmatter or {}).get("launch") or "")
        if frontmatter is None:
            frontmatter = {}
            project = project_by_key(key) if key else None
            path = (project or {}).get("note_path") or ""
            if path and notes.lp(path).is_file():
                text, _nl, _bom, _enc = notes.read_text(path)
                data, _raw, _body = notes.split_frontmatter(text)
                frontmatter = data if isinstance(data, dict) else {}
        overrides = frontmatter.get("task_launch") if isinstance(frontmatter, dict) else None
        keys = _launch_keys(task_id)
        if isinstance(overrides, dict):
            for candidate in keys:
                if candidate in overrides:
                    return resolve_launch(overrides.get(candidate) or "")
        for candidate in keys:
            if template_launch.get(candidate):
                return resolve_launch(template_launch[candidate])
        return ""

    def template_spec(name: str) -> dict:
        spec = (cfg.get("file_templates") or {}).get(str(name or "").strip())
        return spec if isinstance(spec, dict) else {}

    def template_tokens(key: str, task_id: str) -> dict:
        project = project_by_key(key) if key else None
        if project is not None:
            so = [str(item) for item in project.get("so") or []]
            qte = [str(item) for item in project.get("qte") or []]
            project_no = str(project.get("project_no") or "")
            customer = str(project.get("customer") or "")
            folder = Path(str(project.get("folder") or ""))
        else:
            row = personal_task_row(task_id) or {}
            so, qte = _ref_buckets(str(row.get("ref") or ""))
            project_no = ""
            customer = ""
            path = ensure_task_note(task_id, str(row.get("title") or task_id))
            data, _raw, _body = notes.split_frontmatter(notes.read_text(path)[0])
            folder = task_note_folder(data, path)
        ident = so[0] if so else qte[0] if qte else project_no
        return {
            "ident": ident,
            "so": so[0] if so else "",
            "qte": qte[0] if qte else "",
            "project_no": project_no,
            "customer": customer,
            "date": date.today().isoformat(),
            "project_folder": str(folder),
        }

    def fill_tokens(text: str, tokens: dict) -> str:
        def replace(match):
            value = str(tokens.get(match.group(1)) or "").strip()
            if not value:
                raise ValueError("this project has no %s" % match.group(1))
            return value

        return re.sub(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", replace, str(text or ""))

    def template_destinations(spec: dict, tokens: dict, source: Path) -> list[Path]:
        pattern = str(spec.get("name") or "").strip()
        if not pattern:
            raise ValueError("template has no name pattern")
        name = NOTE_BAD_RE.sub("", fill_tokens(pattern, tokens)).strip().strip(".")
        if not Path(name).suffix:
            name = name + source.suffix
        folders = spec.get("folders") or ["{project_folder}"]
        dests = []
        for entry in folders:
            folder = Path(os.path.expandvars(fill_tokens(str(entry), tokens)))
            if not notes.lp(folder).is_dir():
                raise ValueError("folder not found: %s" % folder)
            dests.append(folder / name)
        return dests

    def copy_template(source: Path, dest: Path) -> bool:
        if notes.lp(dest).exists():
            return False
        tmp = dest.with_name("%s.%s.tmp" % (dest.name, secrets.token_hex(4)))
        try:
            shutil.copy2(source, tmp)
            os.replace(tmp, dest)
        except OSError:
            try:
                notes.lp(tmp).unlink()
            except OSError:
                pass
            raise
        return True

    def ensure_project_note(project: dict) -> str:
        """The project's note path, creating a minimal note in its folder when it has none."""
        path = project.get("note_path") or ""
        if path and notes.lp(path).is_file():
            return path
        folder = project.get("folder") or ""
        if not folder or not notes.lp(folder).is_dir():
            raise FileNotFoundError("project folder missing")
        created, _new = projects_mod.create_note_for_folder(folder, project.get("customer") or "")
        project["note_path"] = str(created)
        return str(created)

    def project_note(project: dict) -> str:
        try:
            return ensure_project_note(project)
        except OSError:
            return ""

    def patch_snapshot(changes: dict[str, dict]) -> None:
        """Apply per-project field changes to snapshot.json so pages show them before the next refresh."""
        try:
            data = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for project in data.get("projects") or []:
            update = changes.get(project.get("key"))
            if update:
                project.update(update)
        tmp = snapshot_path.with_name(snapshot_path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, snapshot_path)

    def project_file_label(name: str) -> str:
        group = project_files.group_by_id(cfg, name)
        if group:
            return group["label"]
        item = project_files.item_by_id(cfg, name)
        return item["label"] if item else name

    def file_choices(payload: dict) -> dict | None:
        """Group picks sent by the hub; None keeps the automatic behaviour when nothing is configured."""
        raw = payload.get("choices")
        if raw is None and not project_files.specs(cfg)[1]:
            return None
        return raw if isinstance(raw, dict) else {}

    def project_frontmatter(project: dict) -> dict:
        path = project.get("note_path") or ""
        if not path or not notes.lp(path).is_file():
            return {}
        data, _raw, _body = notes.split_frontmatter(notes.read_text(path)[0])
        return data if isinstance(data, dict) else {}

    def project_file_tokens(project: dict) -> dict:
        return project_files.tokens_for(
            project.get("customer") or "", project.get("so") or [], project.get("qte") or [], project.get("project_no") or ""
        )

    def save_file_choice(project: dict, group_id: str, value: str) -> None:
        path = project_note(project)
        if not path:
            raise FileNotFoundError("note missing")
        label = project_files.frontmatter_values(cfg, {group_id: value})[group_id]
        with mutex:
            with locking.file_lock(lock_file):
                text, newline, bom, encoding = notes.read_text(path)
                data, _raw, body = notes.split_frontmatter(text)
                data = data if isinstance(data, dict) else {}
                data[group_id] = label
                notes.write_text(path, notes.dump_frontmatter(data) + body, newline, bom, encoding)

    def open_project_file(key: str, name: str) -> dict:
        """Open a project file (an item id or a group id), copying it from the template if missing."""
        project = project_by_key(key)
        if project is None:
            raise LookupError("unknown project")
        item = project_files.item_by_id(cfg, name)
        group = project_files.group_by_id(cfg, name)
        if item is None and group is not None:
            choice = project_files.read_choices(cfg, project_frontmatter(project)).get(group["id"])
            if not choice:
                raise ValueError("Choose a %s on the project page first" % group["label"].lower())
            if choice == project_files.NONE:
                raise ValueError("No %s for this project (set to None)" % group["label"].lower())
            item = project_files.item_by_id(cfg, choice)
        if item is None:
            raise LookupError("unknown project file: %s" % name)
        if item["group"]:
            current = project_files.read_choices(cfg, project_frontmatter(project)).get(item["group"])
            if current != item["id"]:
                save_file_choice(project, item["group"], item["id"])
        path, created = project_files.ensure_file(cfg, item, project.get("folder") or "", project_file_tokens(project))
        opener = app.config.get("LAUNCHER") or launcher
        opener(str(path))
        return {"launched": str(path), "created": [str(path)] if created else [], "existing": [] if created else [str(path)]}

    def make_from_template(name: str, key: str, task_id: str) -> dict:
        spec = template_spec(name)
        if not spec:
            raise LookupError("unknown template: %s" % name)
        raw = str(spec.get("source") or "").strip()
        source = Path(os.path.expandvars(raw))
        if not raw or not notes.lp(source).is_file():
            raise FileNotFoundError("template not found: %s" % source)
        tokens = template_tokens(key, task_id)
        dests = template_destinations(spec, tokens, source)
        source_key = os.path.normcase(str(Path(source).resolve()))
        for dest in dests:
            if os.path.normcase(str(Path(dest).resolve())) == source_key:
                raise ValueError("destination is the template: %s" % dest)
        created = []
        existing = []
        for dest in dests:
            if copy_template(source, dest):
                apply_fill(dest, spec.get("fill") or {}, tokens)
                created.append(str(dest))
            else:
                existing.append(str(dest))
        primary = str(dests[0])
        opener = app.config.get("LAUNCHER") or launcher
        opener(primary)
        return {"launched": primary, "created": created, "existing": existing}

    def mutate_state(fn):
        with mutex:
            with locking.file_lock(lock_file):
                data = state_mod.load(state_path)
                result = fn(data)
                state_mod.save(state_path, data)
                return result

    def run_tracker() -> int:
        while True:
            with mutex:
                run_state["running"] = True
                run_state["queued"] = False
                try:
                    exit_code = int(run_hook())
                except Exception as exc:
                    exit_code = -1
                    output = "hub runner failed: %s\n" % exc
                    run_log.parent.mkdir(parents=True, exist_ok=True)
                    with open(run_log, "a", encoding="utf-8") as fh:
                        fh.write(output)
                run_state["last_run"] = datetime.now().astimezone().isoformat(timespec="seconds")
                run_state["last_exit"] = exit_code
                queued = run_state["queued"]
                if not queued:
                    run_state["running"] = False
            notify_new_pending()
            if not queued:
                return exit_code

    def subprocess_runner() -> int:
        proc = subprocess.run(
            [sys.executable, "-m", "tracker.tracker", "--config", cfg_path],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=600,
            creationflags=CREATE_NO_WINDOW,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        run_log.parent.mkdir(parents=True, exist_ok=True)
        with open(run_log, "a", encoding="utf-8") as fh:
            fh.write("=== hub run %s ===\n" % datetime.now().astimezone().isoformat(timespec="seconds"))
            fh.write(output)
            fh.write("\n")
        return proc.returncode

    run_hook = runner or subprocess_runner

    def toast(text: str) -> None:
        if not toast_enabled:
            return
        env = dict(os.environ, HUB_TOAST_TEXT=text)
        try:
            subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-WindowStyle",
                    "Hidden",
                    "-Command",
                    _toast_script(),
                ],
                env=env,
                capture_output=True,
                timeout=30,
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception:
            pass

    def notify_new_pending() -> None:
        snap = load_snapshot()
        current = {
            entry.get("message_id")
            for entry in snap.get("pending") or []
            if (entry.get("status") or "new") == "new"
        }
        fresh = current - seen_pending
        seen_pending.update(current)
        if not fresh:
            return
        for entry in snap.get("pending") or []:
            if entry.get("message_id") in fresh:
                toast("New project assignment: %s" % entry.get("subject", ""))

    def start_run() -> bool:
        if run_state["running"]:
            run_state["queued"] = True
            return False
        threading.Thread(target=run_tracker, daemon=True).start()
        return True

    def refresh_loop() -> None:
        run_tracker()
        last = time.monotonic()
        while True:
            time.sleep(5)
            minutes = live["refresh_minutes"]
            if minutes <= 0 or run_state["running"]:
                continue
            if time.monotonic() - last < minutes * 60:
                continue
            run_tracker()
            last = time.monotonic()

    def note_view(project: dict) -> dict:
        path = project.get("note_path") or ""
        empty = {"text": "", "lines": [], "entries": {}, "hash": "", "exists": False}
        if not path or not notes.lp(path).is_file():
            return empty
        raw = notes.lp(path).read_bytes()
        text, _nl, _bom, _enc = notes.read_text(path)
        _data, _raw, body = notes.split_frontmatter(text)
        entries, _user = notes.parse_block(body)
        return {
            "text": text,
            "lines": render_lines(text),
            "entries": entries,
            "hash": hashlib.sha256(raw).hexdigest(),
            "exists": True,
        }

    def render_lines(text: str) -> list[dict]:
        rows: list[dict] = []
        hidden = False
        for index, line in enumerate(text.split("\n")):
            stripped = line.strip()
            if stripped.startswith("<!-- tracker:start"):
                hidden = True
                continue
            if hidden:
                if stripped == notes.BLOCK_END:
                    hidden = False
                continue
            match = CHECKBOX_RE.match(line)
            if match:
                ident = ID_RE.search(line)
                rows.append(
                    {
                        "index": index,
                        "kind": "check",
                        "mark": match.group(2).lower(),
                        "line": line,
                        "glyph": CHECK_GLYPHS.get(match.group(2).lower(), "\u2610"),
                        "html": Markup(_inline(match.group(3)[1:].strip())),
                        "task_id": ident.group(1) if ident else "",
                    }
                )
                continue
            heading = HEADING_RE.match(line)
            if heading:
                rows.append(
                    {
                        "index": index,
                        "kind": "heading",
                        "level": min(len(heading.group(1)) + 2, 6),
                        "html": Markup(_inline(heading.group(2))),
                    }
                )
                continue
            rows.append({"index": index, "kind": "text", "html": Markup(_inline(line))})
        return rows

    def group_by_stage(rows: list[dict], order: list[str] | None = None) -> list[dict]:
        buckets: dict[str, list[dict]] = {}
        for row in rows:
            buckets.setdefault(row["stage"], []).append(row)
        ordered = []
        for stage in order if order is not None else stage_order:
            if stage in buckets:
                ordered.append({"stage": stage, "tasks": buckets.pop(stage)})
        for stage, tasks in buckets.items():
            ordered.append({"stage": stage or "Other", "tasks": tasks})
        return ordered

    def group_by_category(rows: list[dict]) -> list[dict]:
        groups = []
        for category, label in tasks_mod.CATEGORIES:
            tasks = [row for row in rows if row["category"] == category]
            if tasks:
                groups.append({"id": category, "label": label, "tasks": tasks})
        return groups

    def project_links(project: dict, state: dict) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        for title, url in (
            ("Project page", project.get("sharepoint")),
            ("CPR", project.get("cpr")),
            ("Risk & Issues", project.get("rmt")),
        ):
            if url and url not in seen and _is_http(url):
                seen.add(url)
                out.append({"title": title, "url": url})
        for link in project.get("links") or []:
            url = link.get("url") or ""
            if url and url not in seen and _is_http(url):
                seen.add(url)
                out.append({"title": link.get("title") or url, "url": url})
        for link in (state.get("user_links") or {}).get(project.get("key")) or []:
            url = link.get("url") or ""
            if url and url not in seen and _is_http(url):
                seen.add(url)
                out.append({"title": link.get("title") or url, "url": url, "user": True})
        return out

    def rel_folder(folder: str | Path) -> str:
        return matcher.relative_folder(live["vault"], folder)

    def folder_choices() -> list[str]:
        names = [
            rel_folder(project.get("folder"))
            for project in snapshot().get("projects") or []
            if project.get("folder")
        ]
        names.extend(rel_folder(folder) for folder in vault_dirs())
        return sorted({name for name in names if name}, key=str.lower)

    def vault_dirs() -> list[Path]:
        return [
            folder
            for folder, _customer in matcher.project_dirs(
                live["vault"], cfg.get("exclude_folders") or [], matcher.folder_layout(cfg)
            )
        ]

    def vault_folder_for(entry: dict) -> str:
        parsed = entry.get("parsed") or {}
        wanted = {str(value) for value in [parsed.get("project_no")] + list(parsed.get("so") or []) + list(parsed.get("qte") or []) if value}
        suggested = {
            str(name).strip().lower()
            for name in (entry.get("suggested_folder"), projects_mod.suggested_folder(cfg, entry))
            if name
        }
        for folder in vault_dirs():
            if rel_folder(folder).lower() in suggested:
                return str(folder)
            if _key_for_folder(folder.name) in wanted:
                return str(folder)
        return ""

    def vault_folder_for_key(key: str) -> str:
        for folder in vault_dirs():
            if _key_for_folder(folder.name) == key:
                return str(folder)
        return ""

    def pending_new() -> list[dict]:
        state = state_mod.load(state_path)
        rows = [
            dict(entry, suggested_folder=projects_mod.suggested_folder(cfg, entry))
            for entry in (state.get("pending") or {}).values()
            if (entry.get("status") or "new") == "new"
        ]
        rows.sort(key=lambda entry: str(entry.get("received") or ""), reverse=True)
        return rows

    def backup_root() -> Path:
        raw = cfg.get("backup_root") or (ROOT / "backups")
        path = Path(raw)
        if not path.is_absolute():
            path = ROOT / path
        return path

    def deleted_root() -> Path:
        return backup_root() / "deleted_projects"

    def direct_child(name: str) -> bool:
        text = str(name or "")
        if not text or text in (".", ".."):
            return False
        if "/" in text or "\\" in text:
            return False
        return Path(text).name == text

    def project_rel(name: str) -> bool:
        """A vault-relative project folder: "Name" or "Customer/Name"."""
        text = str(name or "")
        parts = text.replace("\\", "/").split("/")
        return 1 <= len(parts) <= 2 and all(direct_child(part) for part in parts)

    def move_folder(source: Path, target: Path, stop: Path | None = None) -> None:
        notes.lp(target.parent).mkdir(parents=True, exist_ok=True)
        shutil.move(str(notes.lp(source)), str(notes.lp(target)))
        parent = source.parent
        if stop is not None and parent != stop and projects_mod._inside(stop, parent):
            try:
                notes.lp(parent).rmdir()
            except OSError:
                pass

    def archived_names() -> list[str]:
        base = live["vault"] / matcher.ARCHIVE_DIR
        return sorted(
            (
                matcher.relative_folder(base, folder)
                for folder, _customer in matcher.project_dirs(base, (), matcher.folder_layout(cfg))
            ),
            key=str.lower,
        )

    def ui_prefs() -> dict:
        return _ui_prefs(state_mod.load(state_path).get("ui") or {})

    def current_layout() -> str:
        query = request.args.get("layout") or ""
        if query in LAYOUTS:
            return query
        return ui_prefs()["layout"]

    def live_tasks(today: date, archived: bool = False) -> list[dict]:
        cache_name = "_hub_live_archived" if archived else "_hub_live_tasks"
        try:
            cached = getattr(g, cache_name, None)
        except RuntimeError:
            cached = None
        if cached is not None:
            return cached
        state = state_mod.load(state_path)
        overrides = state.get("due_overrides") or {}
        items: list[dict] = []

        def finish(row: dict) -> dict:
            due = row.get("due")
            mark = row.get("mark") or " "
            override = overrides.get(row.get("id") or "") or {}
            override_due = _iso(override.get("due"))
            entries = row.get("time") or []
            tracked = round(sum(hours for _when, hours in entries), 2)
            row.update(
                {
                    "uid": "%s:%s" % (row["key"], row.get("id") or "L%d" % (row.get("line_index") or 0)),
                    "start": row["start"].isoformat() if row.get("start") else None,
                    "due": due.isoformat() if due else None,
                    "computed_due": row["computed_due"].isoformat() if row.get("computed_due") else None,
                    "override_due": override_due.isoformat() if override_due else None,
                    "override_note": override.get("note") or "",
                    "mark": mark,
                    "status": tasks_mod.status_for(mark, due, today, live["due_soon_days"]),
                    "days": (due - today).days if due else None,
                    "hours": row.get("hours"),
                    "hours_label": ("%g" % row["hours"]) if row.get("hours") else "",
                    "worked_days": [item.isoformat() for item in row.get("worked_days") or []],
                    "time_entries": [
                        {"day": when.isoformat(), "hours": hours} for when, hours in entries
                    ],
                    "tracked": tracked,
                    "tracked_label": ("%g" % tracked) if tracked else "",
                    "ref": row.get("ref") or "",
                }
            )
            return row

        def launch_fields(key: str, task_id: str, overrides: dict) -> dict:
            target = launch_target(key, task_id, {"task_launch": overrides})
            if task_id and any(candidate in overrides for candidate in _launch_keys(task_id)):
                source = "project"
            elif target:
                source = "template"
            else:
                source = ""
            return {
                "launch": target,
                "launch_label": launch_label_for(target),
                "launch_source": source,
                **launch_meta(target),
            }

        for project in snapshot().get("projects") or []:
            key = project.get("key") or ""
            name = project_label(project)
            customer = project.get("customer") or ""
            ident = project_ident(project)
            project_stage = project.get("stage") or ""
            parsed: list[dict] = []
            launch_overrides: dict = {}
            path = project.get("note_path") or ""
            if path and notes.lp(path).is_file():
                text, _nl, _bom, _enc = notes.read_text(path)
                parsed = tasks_mod.parse_lines(text, archived=archived)
                data, _raw, _body = notes.split_frontmatter(text)
                if isinstance(data.get("task_launch"), dict):
                    launch_overrides = data["task_launch"]
            by_id: dict[str, dict] = {}
            for row in parsed:
                if row["id"]:
                    by_id.setdefault(row["id"], row)
            for task in [] if archived else project.get("tasks") or []:
                task_id = task.get("id") or ""
                entry = by_id.get(task_id)
                stage = task.get("stage") or ""
                items.append(
                    finish(
                        {
                            "key": key,
                            "project_name": name,
                            "customer": customer,
                            "project_no": project.get("project_no") or "",
                            "ident": ident,
                            "project_stage": project_stage,
                            "kind": "pm" if stage == notes.PM_HEADING else "workflow",
                            "id": task_id,
                            "title": task.get("title") or "",
                            "notes": entry["notes"] if entry else "",
                            "stage": stage,
                            "category": "project",
                            "category_label": tasks_mod.CATEGORY_LABELS["project"],
                            "recur": "",
                            "recur_label": "",
                            "start": _iso(task.get("start")),
                            "due": entry["due"] if entry else _iso(task.get("due")),
                            "computed_due": _iso(task.get("due")),
                            "line_index": entry["index"] if entry else None,
                            "line": entry["line"] if entry else "",
                            "mark": entry["mark"] if entry else str(task.get("mark") or " ").lower(),
                            "hours": entry["hours"] if entry else None,
                            "worked_days": entry["days"] if entry else [],
                            "time": entry["time"] if entry else [],
                            "ref": entry["ref"] if entry else "",
                            **launch_fields(key, task_id, launch_overrides),
                        }
                    )
                )
            for row in parsed:
                if row["in_block"]:
                    continue
                if row["id"].startswith(tasks_mod.MANUAL_PREFIX):
                    kind = "manual"
                elif row["due"] is not None:
                    kind = "note"
                else:
                    continue
                items.append(
                    finish(
                        {
                            "key": key,
                            "project_name": name,
                            "customer": customer,
                            "project_no": project.get("project_no") or "",
                            "ident": ident,
                            "project_stage": project_stage,
                            "kind": kind,
                            "id": row["id"],
                            "title": row["title"],
                            "notes": row["notes"],
                            "stage": "My tasks",
                            "category": "project",
                            "category_label": tasks_mod.CATEGORY_LABELS["project"],
                            "recur": row["recur"],
                            "recur_label": tasks_mod.short_label(row["recur"]),
                            "start": None,
                            "due": row["due"],
                            "computed_due": None,
                            "line_index": row["index"],
                            "line": row["line"],
                            "mark": row["mark"],
                            "hours": row["hours"],
                            "worked_days": row["days"],
                            "time": row["time"],
                            "ref": row["ref"],
                            **launch_fields(key, row["id"], launch_overrides),
                        }
                    )
                )
        if notes.lp(live["personal_path"]).is_file():
            text, _nl, _bom, _enc = notes.read_text(live["personal_path"])
            for row in tasks_mod.parse_lines(text, archived=archived):
                if row["id"].startswith(tasks_mod.MANUAL_PREFIX):
                    kind = "manual"
                elif row["due"] is not None:
                    kind = "note"
                else:
                    continue
                category = row["category"] or "personal"
                label = tasks_mod.CATEGORY_LABELS.get(category, tasks_mod.CATEGORY_LABELS["personal"])
                launch = ""
                if row["note"]:
                    note_path = Path(row["note"])
                    if not note_path.is_absolute():
                        note_path = task_note_root() / note_path
                    if notes.lp(note_path).is_file():
                        note_data, _nraw, _nbody = notes.split_frontmatter(notes.read_text(note_path)[0])
                        launch = str(note_data.get("launch") or "")
                items.append(
                    finish(
                        {
                            "key": "",
                            "project_name": label,
                            "customer": "",
                            "project_no": "",
                            "ident": "",
                            "project_stage": "",
                            "kind": kind,
                            "id": row["id"],
                            "title": row["title"],
                            "notes": row["notes"],
                            "stage": label,
                            "category": category,
                            "category_label": label,
                            "recur": row["recur"],
                            "recur_label": tasks_mod.short_label(row["recur"]),
                            "start": None,
                            "due": row["due"],
                            "computed_due": None,
                            "line_index": row["index"],
                            "line": row["line"],
                            "mark": row["mark"],
                            "hours": row["hours"],
                            "worked_days": row["days"],
                            "time": row["time"],
                            "ref": row["ref"],
                            "ref_suggested": tasks_mod.suggest_ref(row["title"], row["notes"]),
                            "launch": launch,
                            "launch_label": launch_label_for(launch),
                            "launch_source": "task" if launch else "",
                            **launch_meta(launch),
                        }
                    )
                )
        items.sort(key=lambda item: (item["due"] is None, item["due"] or "", item["project_name"], item["title"]))
        setattr(g, cache_name, items)
        return items

    def archived_tasks() -> list[dict]:
        items: list[dict] = []
        for project in snapshot().get("projects") or []:
            path = project.get("note_path") or ""
            if not path or not notes.lp(path).is_file():
                continue
            text, _nl, _bom, _enc = notes.read_text(path)
            for row in tasks_mod.parse_lines(text, archived=True):
                if not row["id"].startswith(tasks_mod.MANUAL_PREFIX):
                    continue
                items.append(
                    {
                        "key": project.get("key") or "",
                        "id": row["id"],
                        "title": row["title"],
                        "project_name": project_label(project),
                        "mark": row["mark"],
                    }
                )
        if notes.lp(live["personal_path"]).is_file():
            text, _nl, _bom, _enc = notes.read_text(live["personal_path"])
            for row in tasks_mod.parse_lines(text, archived=True):
                if not row["id"].startswith(tasks_mod.MANUAL_PREFIX):
                    continue
                category = row["category"] or "personal"
                items.append(
                    {
                        "key": "",
                        "id": row["id"],
                        "title": row["title"],
                        "project_name": tasks_mod.CATEGORY_LABELS.get(
                            category, tasks_mod.CATEGORY_LABELS["personal"]
                        ),
                        "mark": row["mark"],
                    }
                )
        return items

    def task_row(item: dict) -> dict:
        return {
            "project": {"key": item["key"], "name": item["project_name"]},
            "task": {"id": item["id"], "title": item["title"]},
            "due": _iso(item["due"]),
            "days": item["days"],
            "note": item["override_note"],
            "item": item,
        }

    def completed_keys() -> set[str]:
        return {
            str(project.get("key") or "")
            for project in snapshot().get("projects") or []
            if (project.get("stage") or "") == COMPLETION_STAGE
        }

    def open_tasks(today: date) -> list[dict]:
        """Live tasks without the projects in Completion (they only show as a count on the board)."""
        done = completed_keys()
        return [item for item in live_tasks(today) if item["key"] not in done]

    def gantt_rows(today: date) -> list[dict]:
        rows = []
        for item in open_tasks(today):
            if not item["due"] or item["mark"] == "-":
                continue
            due = _iso(item["due"])
            start = _iso(item["start"])
            if start is None or start > due:
                start = due
            rows.append(
                {
                    "project": item["project_name"] or "Personal",
                    "key": item["key"],
                    "title": item["title"],
                    "start": start.isoformat(),
                    "due": due.isoformat(),
                    "status": item["status"],
                    "kind": item["kind"],
                }
            )
        return rows

    def kpis(today: date) -> dict:
        items = open_tasks(today)
        monday = today - timedelta(days=today.weekday())
        done_this_week = 0
        for entry in state_mod.load(state_path).get("tick_log") or []:
            when = _iso(entry.get("at"))
            if when and when >= monday and entry.get("mark") == "x":
                done_this_week += 1
        return {
            "overdue": sum(1 for item in items if item["status"] == "overdue"),
            "due_soon": sum(1 for item in items if item["status"] == "due-soon"),
            "open": sum(1 for item in items if item["mark"] == " "),
            "projects": len(snapshot().get("projects") or []) - len(completed_keys()),
            "done_this_week": done_this_week,
        }

    def project_cards(today: date, state: dict) -> list[dict]:
        items = open_tasks(today)
        cards = []
        for project in snapshot().get("projects") or []:
            key = project.get("key") or ""
            if (project.get("stage") or "") == COMPLETION_STAGE:
                continue
            rows = [
                item for item in items if item["key"] == key and item["kind"] in ("workflow", "pm", "manual")
            ]
            counted = [item for item in rows if item["mark"] != "-"]
            done = [item for item in counted if item["mark"] == "x"]
            dated = sorted(
                (item for item in rows if item["mark"] == " " and item["due"]),
                key=lambda item: item["due"],
            )
            ship = _iso(project.get("ship_date"))
            cards.append(
                {
                    "key": key,
                    "name": project.get("name") or "",
                    "customer": project.get("customer") or "",
                    "project_no": project.get("project_no") or "",
                    "ident": project_ident(project),
                    "label": project_label(project),
                    "level": project.get("level") or "",
                    "stage": project.get("stage") or "",
                    "ship_date": ship.isoformat() if ship else "",
                    "ship_days": (ship - today).days if ship else None,
                    "ship_source": project.get("ship_source") or "",
                    "sheet": project.get("sheet") or {},
                    "sheet_mismatch": bool(project.get("sheet_mismatch")),
                    "design_need_by": project.get("design_need_by") or "",
                    "team": list(project.get("team") or []),
                    "total": len(counted),
                    "done": len(done),
                    "progress_pct": int(round(100 * len(done) / len(counted))) if counted else 0,
                    "overdue": sum(1 for item in rows if item["status"] == "overdue"),
                    "due_soon": sum(1 for item in rows if item["status"] == "due-soon"),
                    "next_due": {
                        "title": dated[0]["title"],
                        "due": dated[0]["due"],
                        "days": dated[0]["days"],
                        "item": dated[0],
                    }
                    if dated
                    else None,
                    "links": len(project_links(project, state)),
                }
            )
        return cards

    def board_columns(cards: list[dict], stages: list[str], completed: int = 0) -> list[dict]:
        order = list(lifecycle or stages)
        for card in cards:
            stage = card["stage"] or "Other"
            if stage not in order:
                order.append(stage)
        columns = []
        for stage in order:
            rows = [card for card in cards if (card["stage"] or "Other") == stage]
            if stage == COMPLETION_STAGE:
                columns.append({"stage": stage, "cards": [], "collapsed": True, "count": completed + len(rows)})
            elif rows or stage in lifecycle:
                columns.append({"stage": stage, "cards": rows, "count": len(rows)})
        return columns

    def focus_buckets(today: date, items: list[dict]) -> tuple[list[dict], list[dict]]:
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)
        next_end = week_end + timedelta(days=7)
        buckets = [
            {"label": "Overdue", "tasks": []},
            {"label": "Today", "tasks": []},
            {"label": "This week", "tasks": []},
            {"label": "Next week", "tasks": []},
            {"label": "Later", "tasks": []},
            {"label": "No date", "tasks": []},
        ]
        done_week = []
        for item in items:
            due = _iso(item["due"])
            if item["mark"] == "x":
                if due and week_start <= due <= week_end:
                    done_week.append(item)
                continue
            if item["mark"] != " ":
                continue
            if due is None:
                buckets[5]["tasks"].append(item)
            elif due < today:
                buckets[0]["tasks"].append(item)
            elif due == today:
                buckets[1]["tasks"].append(item)
            elif due <= week_end:
                buckets[2]["tasks"].append(item)
            elif due <= next_end:
                buckets[3]["tasks"].append(item)
            else:
                buckets[4]["tasks"].append(item)
        return buckets, done_week

    def nav_projects() -> list[dict]:
        items = open_tasks(date.today())
        rows = []
        for project in snapshot().get("projects") or []:
            key = project.get("key") or ""
            if (project.get("stage") or "") == COMPLETION_STAGE:
                continue
            mine = [item for item in items if item["key"] == key]
            overdue = sum(1 for item in mine if item["status"] == "overdue")
            soon = sum(1 for item in mine if item["status"] == "due-soon")
            rows.append(
                {
                    "key": key,
                    "name": project.get("name") or key,
                    "customer": project.get("customer") or "",
                    "ident": project_ident(project),
                    "label": project_label(project),
                    "health": "bad" if overdue else "warn" if soon else "good",
                }
            )
        rows.sort(key=lambda row: (row["customer"].lower() or "\uffff", row["ident"].lower()))
        return rows

    @app.before_request
    def guard():
        if request.host not in allowed_hosts:
            return "forbidden", 403
        if request.method == "POST":
            if not request.is_json:
                return "forbidden", 403
            supplied = request.headers.get("X-Hub-Token", "")
            if not secrets.compare_digest(supplied, token):
                return "forbidden", 403
            origin = request.headers.get("Origin")
            if origin and origin not in allowed_origins:
                return "forbidden", 403
        return None

    @app.context_processor
    def inject_globals():
        return {
            "token": token,
            "file_groups": project_files.groups_for_ui(cfg),
            "buckets": lifecycle,
            "generated": snapshot().get("generated") or "",
            "running": run_state["running"],
            "hub_port": port,
            "today": date.today(),
            "due_soon_days": live["due_soon_days"],
            "theme": live["theme"],
            "auto_reload_pages": live["auto_reload_pages"],
            "ui": ui_prefs(),
            "zooms": ZOOMS,
            "nav_projects": nav_projects(),
            "ape_sheet": snapshot().get("ape_sheet") or {},
        }

    @app.get("/")
    def dashboard():
        snap = snapshot()
        state = state_mod.load(state_path)
        today = date.today()
        pending_rows = pending_new()
        items = open_tasks(today)
        overdue = [task_row(item) for item in items if item["status"] == "overdue"]
        soon = [task_row(item) for item in items if item["status"] == "due-soon"]
        upcoming = [task_row(item) for item in items if item["status"] == "upcoming"]
        projects = []
        for project in snap.get("projects") or []:
            if (project.get("stage") or "") == COMPLETION_STAGE:
                continue
            open_rows = [
                item for item in items if item["key"] == (project.get("key") or "") and item["mark"] == " "
            ]
            dated = sorted((item for item in open_rows if item["due"]), key=lambda item: item["due"])
            ship = _iso(project.get("ship_date"))
            projects.append(
                {
                    "project": project,
                    "open_count": len(open_rows),
                    "next_due": dated[0] if dated else None,
                    "ship": ship,
                    "ship_days": (ship - today).days if ship else None,
                }
            )
        stages = rules_mod.workflow_stages(rules) if rules.get("stages") else list(DEFAULT_STAGES)
        unmatched = []
        email_overrides = state.get("email_overrides") or {}
        for entry in snap.get("unmatched") or []:
            item = dict(entry)
            item["override"] = email_overrides.get(entry.get("message_id")) or ""
            unmatched.append(item)
        cards = project_cards(today, state)
        buckets, done_week = focus_buckets(today, items)
        personal = [item for item in items if item["key"] == ""]
        return render_template(
            "dashboard.html",
            pending=pending_rows,
            folders=folder_choices(),
            overdue=overdue,
            soon=soon,
            upcoming=upcoming,
            projects=projects,
            unmatched=unmatched,
            classify_choices=classify_choices,
            today=today,
            tasks=items,
            gantt=gantt_rows(today),
            kpis=kpis(today),
            project_cards=cards,
            board_columns=board_columns(cards, stages + ["Other"], len(completed_keys())),
            buckets=buckets,
            done_week=done_week,
            personal=personal,
            personal_groups=group_by_category(personal),
            archived_personal=[item for item in archived_tasks() if item["key"] == ""],
            stages=stages + ["Other"],
            layout=current_layout(),
        )

    @app.get("/p/<key>")
    def project_page(key: str):
        project = project_by_key(key)
        if project is None:
            folder = vault_folder_for_key(key)
            if folder:
                start_run()
                return render_template(
                    "project_loading.html",
                    folder=Path(folder).name,
                    key=key,
                    layout=current_layout(),
                )
            abort(404)
        state = state_mod.load(state_path)
        view = note_view(project)
        today = date.today()
        tasks = [item for item in live_tasks(today) if item["key"] == key]
        ship = _iso(project.get("ship_date"))
        design = _iso(project.get("design_need_by"))
        files = folder_files(project.get("folder") or "")
        emails = []
        for email in project.get("emails") or []:
            item = dict(email)
            sender = str(email.get("sender") or "")
            item["sender_name"] = sender.split("<")[0].strip() or sender
            emails.append(item)
        stages = rules_mod.workflow_stages(rules) if rules.get("stages") else list(DEFAULT_STAGES)
        gantt = []
        for item in tasks:
            if not item["due"] or item["mark"] == "-":
                continue
            start = item["start"] if item["start"] and item["start"] <= item["due"] else item["due"]
            gantt.append(
                {
                    "project": item["stage"] or "Other",
                    "key": key,
                    "title": item["title"],
                    "start": start,
                    "due": item["due"],
                    "status": item["status"],
                    "kind": item["kind"],
                }
            )
        return render_template(
            "project.html",
            project=project,
            groups=group_by_stage(tasks, ["My tasks"] + stage_order),
            links=project_links(project, state),
            note=view,
            files=files,
            project_file_rows=project_files.status(
                cfg, project.get("folder") or "", project_file_tokens(project), project_frontmatter(project)
            ),
            emails=emails,
            ship=ship,
            ship_days=(ship - today).days if ship else None,
            design=design,
            design_days=(design - today).days if design else None,
            today=today,
            tasks=tasks,
            stages=lifecycle or stages,
            gantt=gantt,
            archived_project=[item for item in archived_tasks() if item["key"] == key],
            layout=current_layout(),
            so_hints=project_so_hints(project),
            so_can_rename=bool(so_rename_target(project, ["SO-000000"])),
            folder_name=Path(project.get("folder") or "").name,
        )

    @app.get("/t/<task_id>")
    def task_page(task_id: str):
        if not task_id.startswith(tasks_mod.MANUAL_PREFIX):
            abort(404)
        today = date.today()
        item = next(
            (row for row in live_tasks(today) if row["key"] == "" and row["id"] == task_id),
            None,
        )
        if item is None:
            abort(404)
        path = ensure_task_note(task_id, item["title"])
        data, _raw, _body = notes.split_frontmatter(notes.read_text(path)[0])
        folder = task_note_folder(data, path)
        launch = launch_target("", task_id)
        return render_template(
            "task.html",
            task=item,
            note=note_view({"note_path": str(path)}),
            links=task_note_links(data),
            folder=str(folder),
            files=folder_files(folder),
            launch=launch,
            launch_label=launch_label_for(launch),
            **launch_meta(launch),
            layout=current_layout(),
        )

    @app.get("/monday")
    def monday_page():
        snap = snapshot()
        state = state_mod.load(state_path)
        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        start = week_start - timedelta(days=7) if today.weekday() == 0 else week_start
        week_end = week_start + timedelta(days=6)
        tick_log = state.get("tick_log") or []
        items = live_tasks(today)
        blocks = []
        text_lines = ["Monday update %s" % today.isoformat(), ""]
        groups = [(project, project.get("key") or "") for project in snap.get("projects") or []]
        groups.append(({"key": "", "name": "Non-project tasks", "stage": "Non-project"}, ""))
        for project, key in groups:
            rows = [item for item in items if item["key"] == key]
            if not rows:
                continue
            open_rows = [item for item in rows if item["mark"] == " "]
            since = []
            for email in project.get("emails") or []:
                when = _iso(email.get("date"))
                if when and when >= start:
                    since.append("%s %s" % (when.isoformat(), email.get("subject") or ""))
            ticks = []
            for entry in tick_log:
                if entry.get("key") != key:
                    continue
                when = _iso(entry.get("at"))
                if when and when >= start:
                    ticks.append(
                        "ticked %s (%s) on %s"
                        % (
                            entry.get("title") or entry.get("task_id") or "task",
                            "N/A" if entry.get("mark") == "-" else "x",
                            when.isoformat(),
                        )
                    )
            due_week = [
                item for item in open_rows if item["due"] and week_start <= _iso(item["due"]) <= week_end
            ]
            overdue = [item for item in open_rows if item["due"] and _iso(item["due"]) < today]
            ship = _iso(project.get("ship_date"))
            block = {
                "project": project,
                "ship": ship,
                "ship_days": (ship - today).days if ship else None,
                "since": since,
                "ticks": ticks,
                "due_week": due_week,
                "overdue": overdue,
            }
            blocks.append(block)
            text_lines.append("%s" % (project.get("name") or key))
            text_lines.append(
                "  stage: %s | ship: %s%s"
                % (
                    project.get("stage") or "",
                    ship.isoformat() if ship else "n/a",
                    " (%d days)" % (ship - today).days if ship else "",
                )
            )
            text_lines.append("  since last Monday:")
            if since or ticks:
                text_lines.extend("    " + line for line in since + ticks)
            else:
                text_lines.append("    nothing new")
            text_lines.append("  due this week:")
            if due_week:
                text_lines.extend(
                    "    %s  %s%s"
                    % (
                        item["due"],
                        "" if item["key"] else "[%s] " % item["category_label"],
                        item["title"],
                    )
                    for item in due_week
                )
            else:
                text_lines.append("    none")
            text_lines.append("  overdue:")
            if overdue:
                text_lines.extend(
                    "    %s  %s%s"
                    % (
                        item["due"],
                        "" if item["key"] else "[%s] " % item["category_label"],
                        item["title"],
                    )
                    for item in overdue
                )
            else:
                text_lines.append("    none")
            text_lines.append("")
        return render_template(
            "monday.html",
            blocks=blocks,
            start=start,
            week_start=week_start,
            week_end=week_end,
            today=today,
            plain="\n".join(text_lines),
            tasks=items,
            layout=current_layout(),
        )

    @app.get("/projects")
    def projects_page():
        snap = snapshot()
        today = date.today()
        items = live_tasks(today)
        rows = []
        for project in snap.get("projects") or []:
            key = project.get("key") or ""
            open_rows = [item for item in items if item["key"] == key and item["mark"] == " "]
            dates = sorted(str(email.get("date") or "") for email in project.get("emails") or [])
            rows.append(
                {
                    "key": key,
                    "name": project.get("name") or key,
                    "customer": project.get("customer") or "",
                    "ident": project_ident(project),
                    "project_no": project.get("project_no") or "",
                    "stage": project.get("stage") or "",
                    "stage_manual": project.get("stage_manual") or "",
                    "open_count": len(open_rows),
                    "last_email": dates[-1][:10] if dates else "",
                    "folder": project.get("folder") or "",
                }
            )
        archived = archived_names()
        deleted = []
        try:
            for entry in sorted(deleted_root().iterdir(), key=lambda p: p.name, reverse=True):
                if entry.is_dir() and not entry.name.startswith("."):
                    deleted.append(entry.name)
        except OSError:
            pass
        return render_template(
            "projects.html",
            rows=rows,
            stage_filter=str(request.args.get("stage") or ""),
            archived=archived,
            deleted=deleted,
            pending=pending_new(),
            backup_root=str(backup_root()),
            folder_layout=matcher.folder_layout(cfg),
            layout=current_layout(),
        )

    @app.get("/timesheet")
    def timesheet_page():
        today = date.today()
        default_start, default_end = timesheet_mod.week_bounds(today)
        start = _iso(request.args.get("start")) or default_start
        end = _iso(request.args.get("end")) or default_end
        if end < start or (end - start).days > 30:
            start, end = default_start, default_end
        state = state_mod.load(state_path)
        data = timesheet_mod.build(
            live_tasks(today) + live_tasks(today, archived=True),
            snapshot(),
            state.get("tick_log") or [],
            state.get("timesheet_edits") or {},
            start,
            end,
        )
        target = float((cfg.get("timesheet") or {}).get("target_hours") or 8)
        columns = []
        for day_iso in data["days"]:
            day = date.fromisoformat(day_iso)
            columns.append(
                {"iso": day_iso, "label": day.strftime("%a %m/%d"), "weekday": day.weekday() < 5}
            )
        totals = {}
        for column in columns:
            value = data["totals"].get(column["iso"], 0.0)
            state_class = ""
            if column["weekday"] and value:
                state_class = "under" if value < target else "over" if value > target else ""
            totals[column["iso"]] = {"hours": value, "class": state_class}
        copy_lines = ["\t".join(["PROJECT_ID"] + [column["label"] for column in columns])]
        for row in data["rows"]:
            cells = []
            for column in columns:
                value = row["cells"].get(column["iso"], {}).get("hours", 0.0)
                cells.append("%g" % value if value else "")
            copy_lines.append("\t".join([row["project_id"] or row["row_id"]] + cells))
        return render_template(
            "timesheet.html",
            rows=data["rows"],
            columns=columns,
            totals=totals,
            grand=data["grand"],
            target=target,
            start=start,
            end=end,
            prev_start=start - timedelta(days=7),
            prev_end=end - timedelta(days=7),
            next_start=start + timedelta(days=7),
            next_end=end + timedelta(days=7),
            sheet_ok=bool((snapshot().get("ape_sheet") or {}).get("ok")),
            copy_text="\n".join(copy_lines),
            layout=current_layout(),
        )

    @app.post("/api/timesheet/cell")
    def api_timesheet_cell():
        payload = request.get_json(silent=True) or {}
        row_id = str(payload.get("row_id") or "").strip()
        if not row_id or len(row_id) > 80:
            return jsonify({"error": "bad row"}), 400
        try:
            day = date.fromisoformat(str(payload.get("day") or "").strip())
        except ValueError:
            return jsonify({"error": "bad day"}), 400
        hours = None
        hours_raw = payload.get("hours")
        if hours_raw not in (None, ""):
            try:
                hours = float(hours_raw)
            except (TypeError, ValueError):
                return jsonify({"error": "bad hours"}), 400
            if not 0 <= hours <= 24:
                return jsonify({"error": "bad hours"}), 400
        composite = "%s|%s" % (row_id, day.isoformat())

        def apply(state: dict):
            edits = state.setdefault("timesheet_edits", {})
            if hours is None:
                edits.pop(composite, None)
            else:
                edits[composite] = hours
            return hours

        mutate_state(apply)
        return jsonify({"ok": True, "row_id": row_id, "day": day.isoformat(), "hours": hours})

    @app.post("/api/timesheet/spread")
    def api_timesheet_spread():
        payload = request.get_json(silent=True) or {}
        row_id = str(payload.get("row_id") or "").strip()
        if not row_id or len(row_id) > 80:
            return jsonify({"error": "bad row"}), 400
        try:
            start = date.fromisoformat(str(payload.get("start") or "").strip())
            end = date.fromisoformat(str(payload.get("end") or "").strip())
        except ValueError:
            return jsonify({"error": "bad range"}), 400
        if end < start or (end - start).days > 30:
            return jsonify({"error": "bad range"}), 400
        try:
            total = float(payload.get("total"))
        except (TypeError, ValueError):
            return jsonify({"error": "bad total"}), 400
        if not 0 <= total <= 24 * 31:
            return jsonify({"error": "bad total"}), 400
        state = state_mod.load(state_path)
        data = timesheet_mod.build(
            live_tasks(date.today()) + live_tasks(date.today(), archived=True),
            snapshot(),
            state.get("tick_log") or [],
            state.get("timesheet_edits") or {},
            start,
            end,
        )
        row = next((item for item in data["rows"] if item["row_id"] == row_id), None)
        active = []
        if row is not None:
            for day_iso in data["days"]:
                entry = row["cells"].get(day_iso) or {}
                if entry.get("auto") or entry.get("hours") or row["activity"].get(day_iso):
                    active.append(day_iso)
        if not active:
            active = [
                day_iso for day_iso in data["days"] if date.fromisoformat(day_iso).weekday() < 5
            ]
        cells = timesheet_mod.spread(total, active)

        def apply(state: dict):
            edits = state.setdefault("timesheet_edits", {})
            for day_iso in data["days"]:
                edits.pop("%s|%s" % (row_id, day_iso), None)
            for day_iso, hours in cells.items():
                if hours:
                    edits["%s|%s" % (row_id, day_iso)] = hours
            return cells

        mutate_state(apply)
        return jsonify({"ok": True, "cells": cells})

    @app.get("/api/status")
    def api_status():
        return jsonify(dict(run_state, generated=load_snapshot().get("generated") or ""))

    @app.get("/api/project/<key>/links")
    def api_project_links(key: str):
        project = project_by_key(key)
        if project is None:
            return jsonify({"error": "unknown project"}), 404
        state = state_mod.load(state_path)
        links = project_links(project, state)
        ident = (project.get("so") or project.get("qte") or [""])[0]
        title = ("%s %s" % (project.get("customer") or "", ident)).strip()
        payload = {
            "key": key,
            "title": title or project.get("name") or key,
            "hub_url": "http://localhost:%d/p/%s" % (port, key),
            "links": [{"title": link["title"], "url": link["url"]} for link in links],
        }
        response = jsonify(payload)
        origin = request.headers.get("Origin") or ""
        if origin.startswith("chrome-extension://"):
            response.headers["Access-Control-Allow-Origin"] = origin
        return response

    @app.get("/api/pending/preview")
    def api_pending_preview():
        message_id = request.args.get("message_id") or ""
        state = state_mod.load(state_path)
        entry = (state.get("pending") or {}).get(message_id)
        if entry is None:
            return jsonify({"error": "unknown pending entry"}), 404
        template_path = templates_dir / "Project Template.md"
        if not template_path.is_file():
            return jsonify({"error": "template missing: %s" % template_path}), 400
        values = projects_mod._template_values(entry, projects_mod._received_date(entry), "Pre-Kickoff")
        text = projects_mod.render_note(
            template_path.read_text(encoding="utf-8"), values, list(entry.get("checklist") or [])
        )
        return jsonify({"text": text})

    @app.post("/api/rerun")
    def api_rerun():
        started = start_run()
        return jsonify({"started": started, "running": run_state["running"]})

    @app.post("/api/pending/create")
    def api_pending_create():
        payload = request.get_json(silent=True) or {}
        message_id = str(payload.get("message_id") or "")
        folder_name = str(payload.get("folder_name") or "").strip()
        already = ""
        info: dict = {}
        with mutex:
            with locking.file_lock(lock_file):
                state = state_mod.load(state_path)
                entry = (state.get("pending") or {}).get(message_id)
                if entry is None or (entry.get("status") or "new") != "new":
                    already = vault_folder_for(entry or {})
                    if not already:
                        return (
                            jsonify(
                                {
                                    "error": "This assignment was already handled or no longer exists - refresh the page"
                                }
                            ),
                            404,
                        )
                else:
                    try:
                        info = projects_mod.create_from_pending(
                            cfg, entry, folder_name or None, choices=file_choices(payload)
                        )
                    except ValueError as exc:
                        return jsonify({"error": str(exc)}), 400
                    projects_mod.mark_pending(state, message_id, "created")
                    state_mod.save(state_path, state)
        start_run()
        if already:
            key = _key_for_folder(already)
            return jsonify(
                {
                    "already": True,
                    "folder": already,
                    "key": key,
                    "key_url": url_for("project_page", key=key),
                }
            )
        key = _key_for_folder(info["folder"])
        return jsonify(
            {
                "folder": info["folder"],
                "note_path": info["note_path"],
                "files_skipped": info.get("files_skipped") or [],
                "key": key,
                "key_url": url_for("project_page", key=key),
            }
        )

    @app.post("/api/projects/create")
    def api_projects_create():
        payload = request.get_json(silent=True) or {}

        def tokens(raw) -> list[str]:
            if isinstance(raw, (list, tuple)):
                items = [str(item) for item in raw]
            else:
                items = re.split(r"[,;\s]+", str(raw or ""))
            return [item.strip().upper() for item in items if item.strip()]

        customer = str(payload.get("customer") or "").strip()
        if not customer:
            return jsonify({"error": "Customer is required"}), 400
        so = tokens(payload.get("so"))
        qte = tokens(payload.get("qte"))
        project_no = str(payload.get("project_no") or "").strip()
        if project_no and not re.fullmatch(r"\d{7}", project_no):
            return jsonify({"error": "Project # must be 7 digits"}), 400
        level = str(payload.get("level") or "").strip().upper()
        if level not in ("", "L1", "L2", "L3"):
            return jsonify({"error": "Level must be L1, L2 or L3"}), 400
        description = str(payload.get("description") or "").strip()
        folder_name = str(payload.get("folder_name") or "").strip()
        me = cfg.get("me") or {}
        entry = {
            "message_id": "manual-%s" % secrets.token_hex(4),
            "manual": True,
            "subject": " | ".join(
                p
                for p in [customer, ", ".join(so) or ", ".join(qte), project_no, description, level]
                if p
            ),
            "received": datetime.now().astimezone().isoformat(timespec="seconds"),
            "parsed": {
                "customer": customer,
                "so": so,
                "qte": qte,
                "project_no": project_no,
                "level": level,
                "description": description,
            },
            "sharepoint": "",
            "team": [str(me.get("name"))] if me.get("name") else [],
            "checklist": [],
            "suggested_folder": matcher.folder_name_for(customer, so, qte, project_no, matcher.folder_layout(cfg)),
            "status": "new",
        }
        with mutex:
            with locking.file_lock(lock_file):
                try:
                    info = projects_mod.create_from_pending(
                        cfg, entry, folder_name or None, choices=file_choices(payload)
                    )
                except ValueError as exc:
                    return jsonify({"error": str(exc)}), 400
        start_run()
        key = _key_for_folder(info["folder"])
        return jsonify(
            {
                "folder": info["folder"],
                "note_path": info["note_path"],
                "files_skipped": info.get("files_skipped") or [],
                "key": key,
                "key_url": url_for("project_page", key=key),
            }
        )

    @app.post("/api/pending/link")
    def api_pending_link():
        payload = request.get_json(silent=True) or {}
        message_id = str(payload.get("message_id") or "")
        folder = str(payload.get("folder") or "").strip()
        already = ""
        info: dict = {}
        with mutex:
            with locking.file_lock(lock_file):
                state = state_mod.load(state_path)
                entry = (state.get("pending") or {}).get(message_id)
                if entry is None or (entry.get("status") or "new") != "new":
                    already = vault_folder_for(entry or {})
                    if not already:
                        return (
                            jsonify(
                                {
                                    "error": "This assignment was already handled or no longer exists - refresh the page"
                                }
                            ),
                            404,
                        )
                else:
                    try:
                        info = projects_mod.link_pending(cfg, entry, folder)
                    except ValueError as exc:
                        return jsonify({"error": str(exc)}), 400
                    projects_mod.mark_pending(state, message_id, "linked")
                    state_mod.save(state_path, state)
        start_run()
        if already:
            key = _key_for_folder(already)
            return jsonify(
                {
                    "already": True,
                    "folder": already,
                    "key": key,
                    "key_url": url_for("project_page", key=key),
                }
            )
        key = _key_for_folder(info["folder"])
        return jsonify(
            {
                "folder": info["folder"],
                "note_path": info["note_path"],
                "created": info["created"],
                "key": key,
                "key_url": url_for("project_page", key=key),
            }
        )

    @app.post("/api/pending/dismiss")
    def api_pending_dismiss():
        payload = request.get_json(silent=True) or {}
        message_id = str(payload.get("message_id") or "")

        def apply(state: dict) -> bool:
            if message_id not in (state.get("pending") or {}):
                return False
            projects_mod.mark_pending(state, message_id, "dismissed")
            return True

        if not mutate_state(apply):
            return jsonify({"error": "unknown pending entry"}), 404
        return jsonify({"message_id": message_id, "status": "dismissed"})

    @app.post("/api/pending/simulate")
    def api_pending_simulate():
        def apply(state: dict) -> dict:
            pending = state.setdefault("pending", {})
            taken = {str(entry.get("suggested_folder") or "").strip().lower() for entry in pending.values()}
            taken |= {rel_folder(folder).strip().lower() for folder in vault_dirs()}
            folder = ""
            project_no = ""
            qte = ""
            for _attempt in range(50):
                project_no = "0299%03d" % secrets.randbelow(1000)
                qte = "QTE-9%05d" % secrets.randbelow(100000)
                folder = matcher.folder_name_for(
                    "TEST Customer", [], [qte], project_no, matcher.folder_layout(cfg)
                )
                if folder.strip().lower() not in taken:
                    break
            else:
                raise ValueError("could not pick a free test number")
            message_id = "test-%s" % secrets.token_hex(4)
            me = cfg.get("me") or {}
            entry = {
                "message_id": message_id,
                "test": True,
                "subject": "Project Assignment - TEST Customer | %s | %s | Test integration | L1"
                % (qte, project_no),
                "received": datetime.now().astimezone().isoformat(timespec="seconds"),
                "parsed": {
                    "customer": "TEST Customer",
                    "so": [],
                    "qte": [qte],
                    "project_no": project_no,
                    "level": "L1",
                    "description": "Test integration",
                },
                "sharepoint": "",
                "team": [str(me.get("name"))] if me.get("name") else [],
                "checklist": ["AE Order Review Checklist"],
                "suggested_folder": folder,
                "status": "new",
            }
            pending[message_id] = entry
            return entry

        try:
            entry = mutate_state(apply)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"ok": True, "message_id": entry["message_id"], "subject": entry["subject"]})

    @app.post("/api/classify")
    def api_classify():
        payload = request.get_json(silent=True) or {}
        message_id = str(payload.get("message_id") or "")
        trigger = str(payload.get("trigger") or "")
        if trigger and trigger != "ignore" and trigger not in trigger_ids:
            return jsonify({"error": "unknown trigger"}), 400

        def apply(state: dict) -> None:
            overrides = state.setdefault("email_overrides", {})
            if trigger:
                overrides[message_id] = trigger
            else:
                overrides.pop(message_id, None)

        mutate_state(apply)
        start_run()
        return jsonify({"message_id": message_id, "trigger": trigger})

    @app.post("/api/due_override")
    def api_due_override():
        payload = request.get_json(silent=True) or {}
        task_id = str(payload.get("task_id") or "")
        if not task_id:
            return jsonify({"error": "task_id required"}), 400
        clear = bool(payload.get("clear"))
        due = str(payload.get("due") or "").strip()
        note = str(payload.get("note") or "").strip()
        if due and _iso(due) is None:
            return jsonify({"error": "bad date"}), 400

        def apply(state: dict):
            overrides = state.setdefault("due_overrides", {})
            if clear:
                overrides.pop(task_id, None)
                return None
            overrides[task_id] = {"due": due or None, "note": note}
            return overrides[task_id]

        result = mutate_state(apply)
        start_run()
        return jsonify({"task_id": task_id, "override": result})

    @app.post("/api/links/add")
    def api_links_add():
        payload = request.get_json(silent=True) or {}
        key = str(payload.get("key") or "")
        url = str(payload.get("url") or "").strip()
        title = str(payload.get("title") or "").strip() or url
        if not _is_http(url):
            return jsonify({"error": "only http/https urls are accepted"}), 400

        def apply(state: dict) -> list:
            links = state.setdefault("user_links", {}).setdefault(key, [])
            if not any(link.get("url") == url for link in links):
                links.append({"title": title, "url": url})
            return links

        links = mutate_state(apply)
        return jsonify({"key": key, "links": links})

    @app.post("/api/links/remove")
    def api_links_remove():
        payload = request.get_json(silent=True) or {}
        key = str(payload.get("key") or "")
        url = str(payload.get("url") or "").strip()

        def apply(state: dict) -> list:
            links = state.setdefault("user_links", {}).get(key) or []
            links = [link for link in links if link.get("url") != url]
            state["user_links"][key] = links
            return links

        links = mutate_state(apply)
        return jsonify({"key": key, "links": links})

    @app.post("/api/task/links/add")
    def api_task_links_add():
        payload = request.get_json(silent=True) or {}
        task_id = str(payload.get("task_id") or "")
        path = task_note_for(task_id)
        if path is None:
            return jsonify({"error": "unknown task"}), 404
        url = str(payload.get("url") or "").strip().strip('"').strip("'").strip()
        title = str(payload.get("title") or "").strip() or url
        if not url:
            return jsonify({"error": "url required"}), 400
        if not _is_http(url) and not notes.lp(url).exists():
            return jsonify({"error": "url must be an http(s) link or an existing path"}), 400
        with mutex:
            with locking.file_lock(lock_file):
                text, newline, bom, encoding = notes.read_text(path)
                data, _raw, body = notes.split_frontmatter(text)
                links = [link for link in data.get("links") or [] if isinstance(link, dict)]
                if not any(str(link.get("url") or "") == url for link in links):
                    links.append({"title": title, "url": url})
                data["links"] = links
                notes.write_text(path, notes.dump_frontmatter(data) + body, newline, bom, encoding)
        return jsonify({"task_id": task_id, "links": task_note_links(data)})

    @app.post("/api/task/links/remove")
    def api_task_links_remove():
        payload = request.get_json(silent=True) or {}
        task_id = str(payload.get("task_id") or "")
        path = task_note_for(task_id)
        if path is None:
            return jsonify({"error": "unknown task"}), 404
        try:
            index = int(payload.get("index"))
        except (TypeError, ValueError):
            return jsonify({"error": "bad index"}), 400
        with mutex:
            with locking.file_lock(lock_file):
                text, newline, bom, encoding = notes.read_text(path)
                data, _raw, body = notes.split_frontmatter(text)
                links = [link for link in data.get("links") or [] if isinstance(link, dict)]
                if index < 0 or index >= len(links):
                    return jsonify({"error": "bad index"}), 400
                del links[index]
                data["links"] = links
                notes.write_text(path, notes.dump_frontmatter(data) + body, newline, bom, encoding)
        return jsonify({"task_id": task_id, "links": task_note_links(data)})

    @app.post("/api/task/folder")
    def api_task_folder():
        payload = request.get_json(silent=True) or {}
        task_id = str(payload.get("task_id") or "")
        path = task_note_for(task_id)
        if path is None:
            return jsonify({"error": "unknown task"}), 404
        raw = str(payload.get("folder") or "").strip().strip('"').strip("'").strip()
        folder = ""
        if raw:
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = task_note_root() / candidate
            if not notes.lp(candidate).is_dir():
                return jsonify({"error": "folder must be an existing directory"}), 400
            folder = str(candidate)
        with mutex:
            with locking.file_lock(lock_file):
                text, newline, bom, encoding = notes.read_text(path)
                data, _raw, body = notes.split_frontmatter(text)
                data["folder"] = folder
                notes.write_text(path, notes.dump_frontmatter(data) + body, newline, bom, encoding)
        return jsonify({"task_id": task_id, "folder": folder or str(path.parent)})

    @app.post("/api/open")
    def api_open():
        payload = request.get_json(silent=True) or {}
        task_id = str(payload.get("task_id") or "")
        if task_id:
            path = task_note_for(task_id)
            if path is None:
                return jsonify({"error": "unknown task"}), 404
            data, _raw, _body = notes.split_frontmatter(notes.read_text(path)[0])
            what = str(payload.get("target") or "")
            if what == "folder":
                target = str(task_note_folder(data, path))
            elif what == "note":
                target = str(path)
            elif what == "link":
                links = task_note_links(data)
                try:
                    index = int(payload.get("index", payload.get("link_index")))
                except (TypeError, ValueError):
                    return jsonify({"error": "bad index"}), 400
                if index < 0 or index >= len(links):
                    return jsonify({"error": "bad index"}), 400
                if links[index]["http"]:
                    return jsonify({"error": "http links open in the browser"}), 400
                target = links[index]["url"]
            else:
                target = ""
            if not target:
                return jsonify({"error": "nothing to open"}), 404
            try:
                os.startfile(target)
            except OSError as exc:
                return jsonify({"error": str(exc)}), 500
            return jsonify({"opened": target})
        project = project_by_key(str(payload.get("key") or ""))
        if project is None:
            return jsonify({"error": "unknown project"}), 404
        what = str(payload.get("what") or "")
        target = project.get("folder") if what == "folder" else project.get("note_path") if what == "note" else None
        if not target:
            return jsonify({"error": "nothing to open"}), 404
        try:
            os.startfile(target)
        except OSError as exc:
            return jsonify({"error": str(exc)}), 500
        return jsonify({"opened": target})

    def email_open_info(message_id: str) -> dict:
        snap = snapshot()
        for entry in snap.get("unmatched") or []:
            if str(entry.get("message_id") or "") == message_id:
                return dict(entry.get("open") or {})
        for project in snap.get("projects") or []:
            for email in project.get("emails") or []:
                if str(email.get("message_id") or "") == message_id:
                    return dict(email.get("open") or {})
        return {}

    @app.post("/api/email/open")
    def api_email_open():
        payload = request.get_json(silent=True) or {}
        message_id = str(payload.get("message_id") or "")
        info = email_open_info(message_id)
        kind = str(info.get("kind") or "")
        if kind == "eml":
            path = Path(str(info.get("path") or ""))
            if not path.is_file() or path.suffix.lower() != ".eml":
                return jsonify({"error": "The .eml file is no longer there: %s" % path}), 404
            target = str(path)
        elif kind == "outlook" and info.get("entry_id"):
            target = str(info["entry_id"])
        else:
            return jsonify({"error": "Refresh needed: this email was listed before 'open' support"}), 404
        opener = app.config.get("EMAIL_OPENER") or email_opener
        try:
            opener(kind, target)
        except Exception as exc:
            if kind == "outlook":
                return jsonify({"error": "Outlook could not open it: %s" % exc}), 500
            return jsonify({"error": str(exc)}), 500
        return jsonify({"opened": target})

    def project_folder(key: str) -> tuple[dict | None, Path | None, str]:
        project = project_by_key(key)
        if project is None:
            return None, None, "unknown project"
        folder = Path(project.get("folder") or "")
        if not str(folder) or not projects_mod._inside(live["vault"], folder):
            return project, None, "folder is outside the vault"
        return project, folder, ""

    @app.post("/api/projects/archive")
    def api_projects_archive():
        payload = request.get_json(silent=True) or {}
        key = str(payload.get("key") or "")
        project, folder, error = project_folder(key)
        if project is None:
            return jsonify({"error": error}), 404
        if folder is None:
            return jsonify({"error": error}), 400
        target = live["vault"] / matcher.ARCHIVE_DIR / rel_folder(folder)
        if notes.lp(target).exists():
            return jsonify({"error": "already archived: %s" % rel_folder(folder)}), 409
        try:
            move_folder(folder, target, stop=live["vault"])
        except OSError as exc:
            return jsonify({"error": "Could not move the folder (is a file open?): %s" % exc}), 500
        start_run()
        return jsonify({"ok": True, "moved_to": str(target)})

    @app.post("/api/projects/delete")
    def api_projects_delete():
        payload = request.get_json(silent=True) or {}
        key = str(payload.get("key") or "")
        project, folder, error = project_folder(key)
        if project is None:
            return jsonify({"error": error}), 404
        if folder is None:
            return jsonify({"error": error}), 400
        if str(payload.get("confirm") or "") != key:
            return jsonify({"error": "confirm must match the project key"}), 400
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        relative = rel_folder(folder)
        target = deleted_root() / ("%s_%s" % (stamp, relative.replace("/", " - ")))
        try:
            move_folder(folder, target, stop=live["vault"])
        except OSError as exc:
            return jsonify({"error": "Could not move the folder (is a file open?): %s" % exc}), 500
        if relative != folder.name:
            try:
                notes.lp(target / ORIGIN_FILE).write_text(relative, encoding="utf-8")
            except OSError:
                pass

        def apply(state: dict) -> None:
            links = state.get("user_links") or {}
            links.pop(key, None)
            state["user_links"] = links
            for entry in (state.get("pending") or {}).values():
                names = {str(entry.get("suggested_folder") or ""), projects_mod.suggested_folder(cfg, entry)}
                if names & {relative, folder.name}:
                    entry["status"] = "deleted"

        mutate_state(apply)
        start_run()
        return jsonify({"ok": True, "moved_to": str(target)})

    @app.post("/api/projects/restore")
    def api_projects_restore():
        payload = request.get_json(silent=True) or {}
        name = str(payload.get("name") or "")
        if not project_rel(name):
            return jsonify({"error": "bad folder name"}), 400
        source = live["vault"] / matcher.ARCHIVE_DIR / name
        if not notes.lp(source).is_dir():
            return jsonify({"error": "not in the archive: %s" % name}), 404
        target = live["vault"] / name
        if notes.lp(target).exists():
            return jsonify({"error": "a folder with that name already exists"}), 409
        try:
            move_folder(source, target, stop=live["vault"] / matcher.ARCHIVE_DIR)
        except OSError as exc:
            return jsonify({"error": "Could not move the folder (is a file open?): %s" % exc}), 500
        start_run()
        return jsonify({"ok": True, "moved_to": str(target)})

    @app.post("/api/projects/restore_deleted")
    def api_projects_restore_deleted():
        payload = request.get_json(silent=True) or {}
        name = str(payload.get("name") or "")
        if not direct_child(name):
            return jsonify({"error": "bad folder name"}), 400
        source = deleted_root() / name
        if not notes.lp(source).is_dir():
            return jsonify({"error": "not in the deleted backup: %s" % name}), 404
        original = name[16:] if re.match(r"^\d{8}_\d{6}_", name) else name
        origin = notes.lp(source / ORIGIN_FILE)
        try:
            original = origin.read_text(encoding="utf-8").strip() or original
        except OSError:
            pass
        if not project_rel(original):
            return jsonify({"error": "bad folder name"}), 400
        target = live["vault"] / original
        if notes.lp(target).exists():
            return jsonify({"error": "a folder with that name already exists"}), 409
        try:
            move_folder(source, target)
            notes.lp(target / ORIGIN_FILE).unlink(missing_ok=True)
        except OSError as exc:
            return jsonify({"error": "Could not move the folder (is a file open?): %s" % exc}), 500
        start_run()
        return jsonify({"ok": True, "moved_to": str(target)})

    @app.post("/api/projects/open_folder")
    def api_projects_open_folder():
        payload = request.get_json(silent=True) or {}
        where = str(payload.get("where") or "")
        name = str(payload.get("name") or "")
        if not (project_rel(name) if where == "archive" else direct_child(name)):
            return jsonify({"error": "bad folder name"}), 400
        if where == "archive":
            base = live["vault"] / matcher.ARCHIVE_DIR
        elif where == "deleted":
            base = deleted_root()
        else:
            return jsonify({"error": "bad location"}), 400
        target = base / name
        if not notes.lp(target).is_dir():
            return jsonify({"error": "folder missing: %s" % name}), 404
        try:
            os.startfile(str(target))
        except OSError as exc:
            return jsonify({"error": str(exc)}), 500
        return jsonify({"opened": str(target)})

    @app.post("/api/note/toggle")
    def api_note_toggle():
        payload = request.get_json(silent=True) or {}
        key = str(payload.get("key") or "")
        task_id = str(payload.get("task_id") or "")
        if task_id:
            note_path = task_note_for(task_id)
            if note_path is None:
                return jsonify({"error": "note missing"}), 404
            path = str(note_path)
            key = ""
        else:
            project = project_by_key(key)
            if project is None:
                return jsonify({"error": "unknown project"}), 404
            path = project.get("note_path") or ""
            if not path or not notes.lp(path).is_file():
                return jsonify({"error": "note missing"}), 404
        try:
            line_index = int(payload.get("line_index"))
        except (TypeError, ValueError):
            return jsonify({"error": "bad line_index"}), 400
        expected = payload.get("expected_line")
        mark = str(payload.get("mark") or "")
        if mark not in (" ", "x", "-"):
            return jsonify({"error": "bad mark"}), 400
        with mutex:
            with locking.file_lock(lock_file):
                text, newline, bom, encoding = notes.read_text(path)
                lines = text.split("\n")
                if line_index < 0 or line_index >= len(lines) or lines[line_index] != expected:
                    return jsonify({"error": "conflict"}), 409
                match = CHECKBOX_RE.match(lines[line_index])
                if not match:
                    return jsonify({"error": "not a checkbox line"}), 400
                new_line = match.group(1) + mark + match.group(3)
                lines[line_index] = new_line
                notes.write_text(path, "\n".join(lines), newline, bom, encoding)
                raw = notes.lp(path).read_bytes()
                state = state_mod.load(state_path)
                ident = ID_RE.search(new_line)
                state.setdefault("tick_log", []).append(
                    {
                        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "key": key,
                        "task_id": ident.group(1) if ident else task_id,
                        "title": _task_title(new_line),
                        "mark": mark,
                    }
                )
                state_mod.save(state_path, state)
        return jsonify({"line": new_line, "hash": hashlib.sha256(raw).hexdigest(), "mark": mark})

    @app.post("/api/note/save")
    def api_note_save():
        payload = request.get_json(silent=True) or {}
        key = str(payload.get("key") or "")
        task_id = str(payload.get("task_id") or "")
        if task_id:
            note_path = task_note_for(task_id)
            if note_path is None:
                return jsonify({"error": "note missing"}), 404
            path = str(note_path)
        else:
            project = project_by_key(key)
            if project is None:
                return jsonify({"error": "unknown project"}), 404
            path = project.get("note_path") or ""
            if not path or not notes.lp(path).is_file():
                return jsonify({"error": "note missing"}), 404
        text = payload.get("text")
        if not isinstance(text, str):
            return jsonify({"error": "text required"}), 400
        base_hash = str(payload.get("base_hash") or "")
        force = bool(payload.get("force"))
        with mutex:
            with locking.file_lock(lock_file):
                current_hash = hashlib.sha256(notes.lp(path).read_bytes()).hexdigest()
                if not force and base_hash != current_hash:
                    current_text, _nl, _bom, _enc = notes.read_text(path)
                    return (
                        jsonify({"error": "conflict", "current_text": current_text, "current_hash": current_hash}),
                        409,
                    )
                _text, newline, bom, encoding = notes.read_text(path)
                notes.write_text(path, text.replace("\r\n", "\n"), newline, bom, encoding)
                new_hash = hashlib.sha256(notes.lp(path).read_bytes()).hexdigest()
        return jsonify({"hash": new_hash})

    def task_path(key: str) -> Path | None:
        if not key:
            return live["personal_path"] if notes.lp(live["personal_path"]).is_file() else None
        project = project_by_key(key)
        if project is None:
            return None
        path = project_note(project)
        return Path(path) if path else None

    def task_ref(value) -> str | None:
        text = str(value or "").strip().upper()
        if not text:
            return ""
        if not tasks_mod.REF_PATTERN.match(text):
            return None
        return text

    def append_personal(text: str, lines: list[str]) -> str:
        body = text if text.endswith("\n") else text + "\n"
        return body + "\n".join(lines) + "\n"

    @app.get("/api/tasks")
    def api_tasks():
        return jsonify({"tasks": live_tasks(date.today())})

    @app.get("/api/gantt")
    def api_gantt():
        return jsonify(gantt_rows(date.today()))

    @app.get("/favicon.ico")
    def favicon():
        return app.send_static_file("favicon.svg")

    plotly_cache: dict[str, str] = {}

    @app.get("/vendor/plotly.min.js")
    def vendor_plotly():
        if "js" not in plotly_cache:
            from plotly.offline import get_plotlyjs

            plotly_cache["js"] = get_plotlyjs()
        response = app.response_class(plotly_cache["js"], mimetype="application/javascript")
        response.headers["Cache-Control"] = "max-age=86400"
        return response

    @app.post("/api/tasks/add")
    def api_tasks_add():
        payload = request.get_json(silent=True) or {}
        key = str(payload.get("key") or "")
        category = str(payload.get("category") or "").strip() or ("project" if key else "personal")
        if category not in tasks_mod.CATEGORY_IDS:
            return jsonify({"error": "bad category"}), 400
        description = re.sub(r"\s+", " ", str(payload.get("description") or "")).strip()
        if not description or len(description) > 300:
            return jsonify({"error": "description must be 1..300 characters"}), 400
        if (
            notes.DUE_EMOJI in description
            or notes.ID_EMOJI in description
            or tasks_mod.RECUR_EMOJI in description
            or tasks_mod.CATEGORY_RE.search(description)
        ):
            return jsonify({"error": "description must not contain task metadata"}), 400
        due_text = str(payload.get("due") or "").strip()
        due = _iso(due_text) if due_text else None
        if due_text and due is None:
            return jsonify({"error": "bad date"}), 400
        repeat_text = str(payload.get("repeat") or "").strip()
        recur = ""
        if repeat_text:
            recur = tasks_mod.canonical_rule(repeat_text) or ""
            if not recur:
                return jsonify({"error": "bad repeat rule"}), 400
            if due is None:
                due = tasks_mod.first_due(recur, date.today())
        note_text = re.sub(r"\s+", " ", str(payload.get("notes") or "")).strip()
        if len(note_text) > 1000:
            return jsonify({"error": "notes must be 1000 characters or fewer"}), 400
        ref = task_ref(payload.get("ref"))
        if ref is None:
            return jsonify({"error": "bad ref"}), 400
        task_id = tasks_mod.new_manual_id()
        lines = [
            tasks_mod.manual_line(
                description, due, task_id, category=category, recur=recur or None, ref=ref or None
            )
        ]
        if note_text:
            lines.append(tasks_mod.notes_line(note_text))
        if category == "project":
            if not key:
                return jsonify({"error": "choose a project"}), 400
            project = project_by_key(key)
            if project is None:
                return jsonify({"error": "unknown project"}), 404
            path = project_note(project)
            if not path:
                return jsonify({"error": "note missing"}), 400
            with mutex:
                with locking.file_lock(lock_file):
                    text, newline, bom, encoding = notes.read_text(path)
                    notes.write_text(path, tasks_mod.insert_manual(text, lines), newline, bom, encoding)
        else:
            key = ""
            with mutex:
                with locking.file_lock(lock_file):
                    if notes.lp(live["personal_path"]).is_file():
                        text, newline, bom, encoding = notes.read_text(live["personal_path"])
                    else:
                        text, newline, bom, encoding = "# My tasks\n\n", "\n", False, "utf-8"
                        notes.lp(live["personal_path"]).parent.mkdir(parents=True, exist_ok=True)
                    notes.write_text(live["personal_path"], append_personal(text, lines), newline, bom, encoding)
        return jsonify({"ok": True, "id": task_id, "uid": "%s:%s" % (key, task_id)})

    @app.post("/api/tasks/update")
    def api_tasks_update():
        payload = request.get_json(silent=True) or {}
        key = str(payload.get("key") or "")
        task_id = str(payload.get("id") or "")
        if not task_id.startswith(tasks_mod.MANUAL_PREFIX):
            return jsonify({"error": "only manual tasks can be updated"}), 400
        description = None
        if "description" in payload:
            description = re.sub(r"\s+", " ", str(payload.get("description") or "")).strip()
            if not description or len(description) > 300:
                return jsonify({"error": "description must be 1..300 characters"}), 400
            if notes.DUE_EMOJI in description or notes.ID_EMOJI in description:
                return jsonify({"error": "description must not contain task metadata"}), 400
        due = tasks_mod.UNSET
        if "due" in payload:
            due_text = str(payload.get("due") or "").strip()
            due = _iso(due_text) if due_text else None
            if due_text and due is None:
                return jsonify({"error": "bad date"}), 400
        note_text = None
        if "notes" in payload:
            note_text = re.sub(r"\s+", " ", str(payload.get("notes") or "")).strip()
            if len(note_text) > 1000:
                return jsonify({"error": "notes must be 1000 characters or fewer"}), 400
        category = tasks_mod.UNSET
        if "category" in payload:
            value = str(payload.get("category") or "").strip()
            if key or value not in tasks_mod.CATEGORY_IDS or value == "project":
                return jsonify({"error": "category can only be changed on non-project tasks"}), 400
            category = value
        recur = tasks_mod.UNSET
        if "repeat" in payload:
            value = str(payload.get("repeat") or "").strip()
            if value:
                recur = tasks_mod.canonical_rule(value) or ""
                if not recur:
                    return jsonify({"error": "bad repeat rule"}), 400
            else:
                recur = ""
        ref = tasks_mod.UNSET
        if "ref" in payload:
            ref = task_ref(payload.get("ref"))
            if ref is None:
                return jsonify({"error": "bad ref"}), 400
        path = task_path(key)
        if path is None:
            return jsonify({"error": "unknown project or note missing"}), 404
        with mutex:
            with locking.file_lock(lock_file):
                text, newline, bom, encoding = notes.read_text(path)
                try:
                    new_text = tasks_mod.update_manual(
                        text,
                        task_id,
                        description=description,
                        due=due,
                        notes=note_text,
                        category=category,
                        recur=recur,
                        ref=ref,
                    )
                except KeyError:
                    return jsonify({"error": "unknown task"}), 404
                notes.write_text(path, new_text, newline, bom, encoding)
        return jsonify({"ok": True, "id": task_id, "uid": "%s:%s" % (key, task_id)})

    @app.post("/api/tasks/delete")
    def api_tasks_delete():
        payload = request.get_json(silent=True) or {}
        key = str(payload.get("key") or "")
        task_id = str(payload.get("id") or "")
        if not task_id.startswith(tasks_mod.MANUAL_PREFIX):
            return jsonify({"error": "only manual tasks can be deleted"}), 400
        path = task_path(key)
        if path is None:
            return jsonify({"error": "unknown project or note missing"}), 404
        with mutex:
            with locking.file_lock(lock_file):
                text, newline, bom, encoding = notes.read_text(path)
                try:
                    new_text = tasks_mod.delete_manual(text, task_id)
                except KeyError:
                    return jsonify({"error": "unknown task"}), 404
                notes.write_text(path, new_text, newline, bom, encoding)
        return jsonify({"ok": True, "id": task_id})

    @app.post("/api/tasks/archive")
    def api_tasks_archive():
        payload = request.get_json(silent=True) or {}
        key = str(payload.get("key") or "")
        task_id = str(payload.get("id") or "")
        if not task_id.startswith(tasks_mod.MANUAL_PREFIX):
            return jsonify({"error": "only manual tasks can be archived"}), 400
        path = task_path(key)
        if path is None:
            return jsonify({"error": "unknown project or note missing"}), 404
        with mutex:
            with locking.file_lock(lock_file):
                text, newline, bom, encoding = notes.read_text(path)
                try:
                    new_text = tasks_mod.archive_manual(text, task_id)
                except KeyError:
                    return jsonify({"error": "unknown task"}), 404
                notes.write_text(path, new_text, newline, bom, encoding)
        return jsonify({"ok": True, "id": task_id})

    @app.post("/api/tasks/unarchive")
    def api_tasks_unarchive():
        payload = request.get_json(silent=True) or {}
        key = str(payload.get("key") or "")
        task_id = str(payload.get("id") or "")
        if not task_id.startswith(tasks_mod.MANUAL_PREFIX):
            return jsonify({"error": "only manual tasks can be archived"}), 400
        path = task_path(key)
        if path is None:
            return jsonify({"error": "unknown project or note missing"}), 404
        with mutex:
            with locking.file_lock(lock_file):
                text, newline, bom, encoding = notes.read_text(path)
                try:
                    new_text = tasks_mod.unarchive_manual(text, task_id)
                except KeyError:
                    return jsonify({"error": "unknown task"}), 404
                notes.write_text(path, new_text, newline, bom, encoding)
        return jsonify({"ok": True, "id": task_id})

    @app.post("/api/task/toggle")
    def api_task_toggle():
        payload = request.get_json(silent=True) or {}
        key = str(payload.get("key") or "")
        task_id = str(payload.get("id") or "")
        mark = str(payload.get("mark") or "")
        if mark not in (" ", "x", "-"):
            return jsonify({"error": "bad mark"}), 400
        line_index = None
        if not task_id:
            try:
                line_index = int(payload.get("line_index"))
            except (TypeError, ValueError):
                return jsonify({"error": "bad line_index"}), 400
        path = task_path(key)
        if path is None:
            return jsonify({"error": "note missing"}), 404
        spawned = None
        with mutex:
            with locking.file_lock(lock_file):
                text, newline, bom, encoding = notes.read_text(path)
                try:
                    new_text, row = tasks_mod.set_mark(
                        text,
                        task_id=task_id or None,
                        line_index=line_index,
                        expected_line=payload.get("expected_line"),
                        mark=mark,
                    )
                except (KeyError, ValueError):
                    return jsonify({"error": "conflict"}), 409
                if task_id.startswith(tasks_mod.MANUAL_PREFIX):
                    new_text, spawn_row = tasks_mod.spawn_next(new_text, task_id, date.today())
                    if spawn_row is not None:
                        spawned = {
                            "id": spawn_row["id"],
                            "due": spawn_row["due"].isoformat() if spawn_row["due"] else None,
                        }
                notes.write_text(path, new_text, newline, bom, encoding)
                state = state_mod.load(state_path)
                state.setdefault("tick_log", []).append(
                    {
                        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "key": key,
                        "task_id": row["id"] or task_id,
                        "title": row["title"],
                        "mark": mark,
                    }
                )
                state_mod.save(state_path, state)
        return jsonify({"ok": True, "mark": mark, "line": row["line"], "spawned": spawned})

    @app.post("/api/task/time")
    def api_task_time():
        payload = request.get_json(silent=True) or {}
        key = str(payload.get("key") or "")
        task_id = str(payload.get("id") or "")
        line_index = None
        if not task_id:
            try:
                line_index = int(payload.get("line_index"))
            except (TypeError, ValueError):
                return jsonify({"error": "bad line_index"}), 400
        hours = None
        hours_raw = payload.get("hours")
        if hours_raw not in (None, ""):
            try:
                hours = float(hours_raw)
            except (TypeError, ValueError):
                return jsonify({"error": "bad hours"}), 400
            if not 0 <= hours <= tasks_mod.MAX_HOURS:
                return jsonify({"error": "bad hours"}), 400
        days_raw = payload.get("days")
        if days_raw in (None, ""):
            days_raw = []
        if not isinstance(days_raw, list):
            return jsonify({"error": "bad days"}), 400
        today = date.today()
        days = []
        for item in days_raw:
            try:
                when = date.fromisoformat(str(item).strip())
            except ValueError:
                return jsonify({"error": "bad days"}), 400
            if when < today - timedelta(days=31) or when > today + timedelta(days=1):
                return jsonify({"error": "bad days"}), 400
            days.append(when)
        if hours and not days:
            days = [today]
        path = task_path(key)
        if path is None:
            return jsonify({"error": "note missing"}), 404
        with mutex:
            with locking.file_lock(lock_file):
                text, newline, bom, encoding = notes.read_text(path)
                try:
                    new_text, row = tasks_mod.set_time(
                        text,
                        task_id=task_id or None,
                        line_index=line_index,
                        expected_line=payload.get("expected_line"),
                        hours=hours,
                        days=days,
                    )
                except (KeyError, ValueError):
                    return jsonify({"error": "conflict"}), 409
                notes.write_text(path, new_text, newline, bom, encoding)
        return jsonify(
            {
                "ok": True,
                "line": row["line"],
                "hours": row["hours"],
                "days": [item.isoformat() for item in row["days"]],
            }
        )

    @app.post("/api/task/time/add")
    def api_task_time_add():
        payload = request.get_json(silent=True) or {}
        key = str(payload.get("key") or "")
        task_id = str(payload.get("id") or "")
        line_index = None
        if not task_id:
            try:
                line_index = int(payload.get("line_index"))
            except (TypeError, ValueError):
                return jsonify({"error": "bad line_index"}), 400
        try:
            hours = float(payload.get("hours"))
        except (TypeError, ValueError):
            return jsonify({"error": "bad hours"}), 400
        if not 0 < hours <= 24:
            return jsonify({"error": "bad hours"}), 400
        today = date.today()
        day_raw = payload.get("day")
        if day_raw in (None, ""):
            day = today
        else:
            try:
                day = date.fromisoformat(str(day_raw).strip())
            except ValueError:
                return jsonify({"error": "bad day"}), 400
            if day < today - timedelta(days=31) or day > today + timedelta(days=1):
                return jsonify({"error": "bad day"}), 400
        path = task_path(key)
        if path is None:
            return jsonify({"error": "note missing"}), 404
        with mutex:
            with locking.file_lock(lock_file):
                text, newline, bom, encoding = notes.read_text(path)
                try:
                    new_text, row = tasks_mod.add_time_entry(
                        text,
                        task_id=task_id or None,
                        line_index=line_index,
                        expected_line=payload.get("expected_line"),
                        day=day,
                        hours=hours,
                    )
                except (KeyError, ValueError):
                    return jsonify({"error": "conflict"}), 409
                notes.write_text(path, new_text, newline, bom, encoding)
        return jsonify(_time_payload(row))

    @app.post("/api/task/time/remove")
    def api_task_time_remove():
        payload = request.get_json(silent=True) or {}
        key = str(payload.get("key") or "")
        task_id = str(payload.get("id") or "")
        line_index = None
        if not task_id:
            try:
                line_index = int(payload.get("line_index"))
            except (TypeError, ValueError):
                return jsonify({"error": "bad line_index"}), 400
        try:
            entry_index = int(payload.get("entry_index"))
        except (TypeError, ValueError):
            return jsonify({"error": "bad entry_index"}), 400
        path = task_path(key)
        if path is None:
            return jsonify({"error": "note missing"}), 404
        with mutex:
            with locking.file_lock(lock_file):
                text, newline, bom, encoding = notes.read_text(path)
                try:
                    new_text, row = tasks_mod.remove_time_entry(
                        text,
                        task_id=task_id or None,
                        line_index=line_index,
                        expected_line=payload.get("expected_line"),
                        entry_index=entry_index,
                    )
                except (KeyError, ValueError):
                    return jsonify({"error": "conflict"}), 409
                notes.write_text(path, new_text, newline, bom, encoding)
        return jsonify(_time_payload(row))

    @app.post("/api/task/launch")
    def api_task_launch():
        payload = request.get_json(silent=True) or {}
        key = str(payload.get("key") or "")
        task_id = str(payload.get("task_id") or "")
        target = launch_target(key, task_id)
        if not target:
            return jsonify({"error": "no shortcut for this task"}), 404
        if target.startswith("file:"):
            try:
                result = open_project_file(key, target[5:].strip())
            except (LookupError, FileNotFoundError) as exc:
                return jsonify({"error": str(exc)}), 404
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
            except OSError as exc:
                return jsonify({"error": str(exc)}), 500
            return jsonify(result)
        if target.startswith("template:"):
            try:
                result = make_from_template(target[9:].strip(), key, task_id)
            except (LookupError, FileNotFoundError) as exc:
                return jsonify({"error": str(exc)}), 404
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
            except OSError as exc:
                return jsonify({"error": str(exc)}), 500
            return jsonify(result)
        if not _is_http(target) and not notes.lp(target).exists():
            return jsonify({"error": "not found: %s" % target}), 404
        opener = app.config.get("LAUNCHER") or launcher
        try:
            opener(target)
        except OSError as exc:
            return jsonify({"error": str(exc)}), 500
        return jsonify({"launched": target})

    @app.post("/api/projects/stage")
    def api_projects_stage():
        payload = request.get_json(silent=True) or {}
        keys = payload.get("keys")
        if isinstance(payload.get("key"), str):
            keys = [payload["key"]]
        if not isinstance(keys, list) or not keys or len(keys) > 1000:
            return jsonify({"error": "choose at least one project"}), 400
        stage = str(payload.get("stage") or "").strip()
        if stage and stage not in lifecycle:
            return jsonify({"error": "unknown stage: %s" % stage}), 400
        changes, failed = apply_stages([(str(key), stage) for key in keys], "")
        if not stage and changes:
            start_run()
        return jsonify({"ok": not failed, "updated": len(changes), "failed": failed, "stage": stage}), (200 if changes or not failed else 400)

    def apply_stages(pairs: list[tuple[str, str]], source: str) -> tuple[dict, list[str]]:
        """Pin each (key, stage) in its note (creating the note if needed) and in snapshot.json."""
        changes: dict[str, dict] = {}
        failed: list[str] = []
        with mutex:
            with locking.file_lock(lock_file):
                for key, stage in pairs:
                    project = project_by_key(key)
                    if project is None:
                        failed.append("%s: unknown project" % key)
                        continue
                    try:
                        path = ensure_project_note(project)
                        projects_mod.set_manual_stage(path, stage, source)
                    except OSError as exc:
                        failed.append("%s: %s" % (project.get("name") or key, exc))
                        continue
                    update = {"note_path": path, "stage_manual": stage, "stage_manual_source": source if stage else ""}
                    if stage:
                        update["stage"] = stage
                    changes[key] = update
                if changes:
                    patch_snapshot(changes)
        return changes, failed

    def so_owner(so: str, key: str) -> dict | None:
        for project in snapshot().get("projects") or []:
            if project.get("key") != key and so in (project.get("so") or []):
                return project
        return None

    def so_rename_target(project: dict, sos: list[str]) -> str:
        folder = Path(project.get("folder") or "")
        if not str(folder) or not projects_mod._inside(live["vault"], folder):
            return ""
        return projects_mod.so_folder_name(folder.name, sos, matcher.folder_layout(cfg))

    def add_sos(key: str, sos: list[str], rename: bool) -> dict:
        """Add SO numbers to a project's note, optionally rename its folder to "SO-... (QTE-...)", and move the
        state kept under the old key. Raises LookupError / ValueError / OSError (nothing changed)."""
        project = project_by_key(key)
        if project is None:
            raise LookupError("unknown project")
        have = list(project.get("so") or [])
        new = sorted(set(sos) - set(have))
        if not new:
            raise ValueError("%s already has %s" % (project.get("name") or key, ", ".join(sos) or "those SOs"))
        warnings: list[str] = []
        for so in new:
            owner = so_owner(so, key)
            if owner is not None:
                warnings.append("%s also has its own folder: %s" % (so, owner.get("name") or owner.get("key")))
        merged_all = sorted(set(have) | set(new))
        new_key = project.get("project_no") or merged_all[0]
        old_prefix = projects_mod.id_prefix_for(project.get("project_no") or "", key)
        folder = Path(project.get("folder") or "")
        note_path = ""
        renamed = ""
        with mutex:
            with locking.file_lock(lock_file):
                note_path = ensure_project_note(project)
                merged = projects_mod.add_so(note_path, new, id_prefix=old_prefix if new_key != key else "")
                target_name = so_rename_target(project, merged) if rename else ""
                if target_name and target_name != folder.name:
                    target = folder.parent / target_name
                    if notes.lp(target).exists():
                        warnings.append("Folder not renamed: %s already exists" % target_name)
                    else:
                        try:
                            move_folder(folder, target)
                        except OSError as exc:
                            warnings.append("Folder not renamed (is a file open?): %s" % exc)
                        else:
                            renamed = target_name
                            old_note = Path(note_path)
                            note_path = str(target / old_note.name)
                            if old_note.stem == folder.name:
                                new_note = target / (target_name + old_note.suffix)
                                try:
                                    notes.lp(target / old_note.name).rename(notes.lp(new_note))
                                    note_path = str(new_note)
                                except OSError:
                                    pass
                            folder = target
                update = {"key": new_key, "so": merged, "note_path": note_path, "folder": str(folder)}
                if renamed:
                    update["name"] = renamed
                update["so_hints"] = [hint for hint in project.get("so_hints") or [] if hint.get("so") not in merged]
                patch_snapshot({key: update})

        if new_key != key:

            def migrate(state: dict) -> None:
                for bucket in ("projects", "user_links"):
                    stored = state.get(bucket) or {}
                    if key in stored and new_key not in stored:
                        stored[new_key] = stored.pop(key)
                    state[bucket] = stored

            mutate_state(migrate)
        return {"key": new_key, "so": merged, "added": new, "renamed": renamed, "warnings": warnings}

    def parse_sos(value) -> list[str]:
        text = " ".join(str(item) for item in value) if isinstance(value, list) else str(value or "")
        return sorted(matcher.extract_keys(text)["so"])

    @app.post("/api/projects/so")
    def api_projects_so():
        payload = request.get_json(silent=True) or {}
        sos = parse_sos(payload.get("so"))
        if not sos:
            return jsonify({"error": "enter SO numbers like SO-285770"}), 400
        try:
            result = add_sos(str(payload.get("key") or ""), sos, payload.get("rename") is not False)
        except LookupError as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except OSError as exc:
            return jsonify({"error": "Could not update the project: %s" % exc}), 500
        start_run()
        return jsonify(dict(result, ok=True))

    @app.post("/api/projects/so/dismiss")
    def api_projects_so_dismiss():
        payload = request.get_json(silent=True) or {}
        key = str(payload.get("key") or "")
        sos = parse_sos(payload.get("so"))
        project = project_by_key(key)
        if project is None:
            return jsonify({"error": "unknown project"}), 404
        if len(sos) != 1:
            return jsonify({"error": "one SO number expected"}), 400
        with mutex:
            with locking.file_lock(lock_file):
                try:
                    path = ensure_project_note(project)
                    projects_mod.dismiss_so(path, sos[0])
                except OSError as exc:
                    return jsonify({"error": "Could not update the note: %s" % exc}), 500
                patch_snapshot(
                    {
                        key: {
                            "note_path": path,
                            "so_dismissed": sorted(set(project.get("so_dismissed") or []) | set(sos)),
                            "so_hints": [hint for hint in project.get("so_hints") or [] if hint.get("so") != sos[0]],
                        }
                    }
                )
        return jsonify({"ok": True, "key": key, "so": sos[0]})

    def project_so_hints(project: dict) -> list[dict]:
        have = set(project.get("so") or []) | set(project.get("so_dismissed") or [])
        out = []
        for hint in project.get("so_hints") or []:
            if hint.get("so") in have:
                continue
            owner = so_owner(str(hint.get("so") or ""), project.get("key") or "")
            out.append(dict(hint, owner=(owner.get("name") or owner.get("key")) if owner else ""))
        return out

    def sync_projects() -> list[dict]:
        out = []
        for project in snapshot().get("projects") or []:
            item = dict(project)
            dates = sorted(str(email.get("date") or "") for email in project.get("emails") or [])
            item["last_email"] = dates[-1][:10] if dates else ""
            out.append(item)
        return out

    @app.post("/api/tracker-sync/preview")
    def api_tracker_sync_preview():
        sheet = ape_sheet.load(cfg, download=True)
        if not sheet.get("ok"):
            return jsonify({"error": "Could not read the Project Tracking sheet: %s" % (sheet.get("error") or "unknown error")}), 502
        rows = sheet.get("rows") or []
        projects = sync_projects()
        proposals = ape_sheet.sync_proposals(rows, projects, date.today(), lifecycle)
        so_items = ape_sheet.so_suggestions(rows, projects, date.today())
        by_key = {project.get("key"): project for project in projects}
        planned: dict[str, list[str]] = {}
        for item in so_items:
            if item["checked"]:
                planned.setdefault(item["key"], []).append(item["so"])
        for item in so_items:
            project = by_key.get(item["key"]) or {}
            sos = sorted(set(project.get("so") or []) | set(planned.get(item["key"]) or []) | {item["so"]})
            item["rename_to"] = so_rename_target(project, sos)
        tabs: dict[str, int] = {}
        for row in rows:
            tabs[row.get("tab") or ""] = tabs.get(row.get("tab") or "", 0) + 1
        return jsonify(
            {
                "proposals": proposals,
                "so_suggestions": so_items,
                "sheet": {
                    "kind": sheet.get("kind") or "",
                    "updated": sheet.get("source_mtime_label") or "",
                    "warning": sheet.get("warning") or "",
                    "tabs": tabs,
                },
            }
        )

    @app.post("/api/tracker-sync/apply")
    def api_tracker_sync_apply():
        payload = request.get_json(silent=True) or {}
        items = payload.get("items") or []
        so_items = payload.get("so_items") or []
        if not isinstance(items, list) or not isinstance(so_items, list) or not (items or so_items) or len(items) + len(so_items) > 5000:
            return jsonify({"error": "nothing selected"}), 400
        pairs = []
        for item in items:
            key = str((item or {}).get("key") or "")
            stage = str((item or {}).get("stage") or "").strip()
            if not key or stage not in lifecycle:
                return jsonify({"error": "unknown stage: %s" % stage}), 400
            pairs.append((key, stage))
        grouped: dict[str, list[str]] = {}
        for item in so_items:
            key = str((item or {}).get("key") or "")
            sos = parse_sos((item or {}).get("so"))
            if not key or not sos:
                return jsonify({"error": "bad SO item"}), 400
            grouped.setdefault(key, []).extend(sos)
        changes, failed = apply_stages(pairs, ape_sheet.SYNC_SOURCE) if pairs else ({}, [])
        added = 0
        renamed = 0
        warnings: list[str] = []
        for key, sos in grouped.items():
            try:
                result = add_sos(key, sos, payload.get("rename") is not False)
            except (LookupError, ValueError, OSError) as exc:
                failed.append("%s: %s" % (key, exc))
                continue
            added += len(result["added"])
            renamed += 1 if result["renamed"] else 0
            warnings.extend(result["warnings"])
        if added:
            start_run()
        ok = bool(changes or added)
        return jsonify(
            {"ok": not failed, "updated": len(changes), "so_added": added, "renamed": renamed, "warnings": warnings, "failed": failed}
        ), (200 if ok or not failed else 400)

    @app.post("/api/projects/files/open")
    def api_project_file_open():
        payload = request.get_json(silent=True) or {}
        try:
            result = open_project_file(str(payload.get("key") or ""), str(payload.get("item") or ""))
        except (LookupError, FileNotFoundError) as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except OSError as exc:
            return jsonify({"error": str(exc)}), 500
        return jsonify(result)

    @app.post("/api/projects/files/choose")
    def api_project_file_choose():
        payload = request.get_json(silent=True) or {}
        project = project_by_key(str(payload.get("key") or ""))
        if project is None:
            return jsonify({"error": "unknown project"}), 404
        group = project_files.group_by_id(cfg, str(payload.get("group") or ""))
        if group is None:
            return jsonify({"error": "unknown group"}), 400
        value = project_files.resolve_option(group, payload.get("option"))
        if value is None:
            return jsonify({"error": "unknown option"}), 400
        if value == project_files.NONE and not group["none_allowed"]:
            return jsonify({"error": "A %s is required" % group["label"].lower()}), 400
        try:
            save_file_choice(project, group["id"], value)
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404
        rows = project_files.status(
            cfg, project.get("folder") or "", project_file_tokens(project), project_frontmatter(project)
        )
        row = next((r for r in rows if r.get("group") and r["group"]["id"] == group["id"]), {})
        return jsonify({"group": group["id"], "choice": value, "file": row.get("file", ""), "exists": row.get("exists", False), "error": row.get("error", "")})

    @app.post("/api/task/launch_set")
    def api_task_launch_set():
        payload = request.get_json(silent=True) or {}
        key = str(payload.get("key") or "")
        task_id = str(payload.get("task_id") or "")
        if not task_id:
            return jsonify({"error": "task_id required"}), 400
        reset = bool(payload.get("reset"))
        target = str(payload.get("target") or "").strip().strip('"').strip("'").strip()
        if not reset and target and not _is_http(target) and not notes.lp(target).exists():
            if target.startswith("file:"):
                if not resolve_launch(target):
                    return jsonify({"error": "unknown project file: %s" % target[5:].strip()}), 400
            elif target.startswith("template:"):
                if not template_spec(target[9:].strip()):
                    return jsonify({"error": "unknown template: %s" % target[9:].strip()}), 400
            elif target.startswith("tool:"):
                if target[5:].strip() not in (cfg.get("tools") or {}):
                    return jsonify({"error": "unknown tool: %s" % target[5:].strip()}), 400
            else:
                return jsonify({"error": "target must be an existing file/folder or an http(s) link"}), 400
        if not key and task_id.startswith(tasks_mod.MANUAL_PREFIX):
            row = personal_task_row(task_id) or {}
            note_path = ensure_task_note(task_id, row.get("title") or task_id)
            with mutex:
                with locking.file_lock(lock_file):
                    text, newline, bom, encoding = notes.read_text(note_path)
                    data, _raw, body = notes.split_frontmatter(text)
                    if reset or not target:
                        data.pop("launch", None)
                    else:
                        data["launch"] = target
                    notes.write_text(note_path, notes.dump_frontmatter(data) + body, newline, bom, encoding)
            resolved = "" if reset or not target else target
            return jsonify({"task_id": task_id, "launch": resolved, "source": "task" if resolved else ""})
        project = project_by_key(key)
        if project is None:
            return jsonify({"error": "unknown project"}), 404
        path = project.get("note_path") or ""
        if not path or not notes.lp(path).is_file():
            return jsonify({"error": "note missing"}), 404
        with mutex:
            with locking.file_lock(lock_file):
                text, newline, bom, encoding = notes.read_text(path)
                data, _raw, body = notes.split_frontmatter(text)
                overrides = data.get("task_launch")
                if not isinstance(overrides, dict):
                    overrides = {}
                if reset:
                    for candidate in _launch_keys(task_id):
                        overrides.pop(candidate, None)
                else:
                    overrides[task_id] = target
                if overrides:
                    data["task_launch"] = overrides
                else:
                    data.pop("task_launch", None)
                if data:
                    new_text = notes.dump_frontmatter(data) + body
                else:
                    new_text = body
                notes.write_text(path, new_text, newline, bom, encoding)
        text, _nl, _bom, _enc = notes.read_text(path)
        data, _raw, _body = notes.split_frontmatter(text)
        overrides = data.get("task_launch") if isinstance(data.get("task_launch"), dict) else {}
        resolved = launch_target(key, task_id, data)
        source = "project" if any(c in overrides for c in _launch_keys(task_id)) else "template" if resolved else ""
        return jsonify({"task_id": task_id, "launch": resolved, "source": source})

    @app.post("/api/ui")
    def api_ui():
        payload = request.get_json(silent=True) or {}
        try:
            update = _ui_update(payload)
        except ValueError as exc:
            return jsonify({"error": "bad %s" % exc}), 400

        def apply(state: dict) -> dict:
            state["ui"] = {**_ui_prefs(state.get("ui") or {}), **update}
            return state["ui"]

        return jsonify({"ok": True, "ui": mutate_state(apply)})

    def effective_settings() -> dict:
        return {
            "vault": str(live["vault"]).replace("\\", "/"),
            "personal_tasks_path": str(live["personal_path"]).replace("\\", "/"),
            "time_estimate_template": str(cfg.get("time_estimate_template") or "").replace("\\", "/"),
            "ape_sheet_path": str(cfg.get("ape_sheet_path") or "").replace("\\", "/"),
            "refresh_minutes": live["refresh_minutes"],
            "due_soon_days": live["due_soon_days"],
            "theme": live["theme"],
            "auto_reload_pages": live["auto_reload_pages"],
            "ui": ui_prefs(),
        }

    @app.get("/settings")
    def settings_page():
        return render_template(
            "settings.html",
            config_path=cfg_path,
            settings_path=str(settings_path),
            settings=effective_settings(),
            layouts=LAYOUTS,
        )

    @app.post("/api/settings")
    def api_settings():
        payload = request.get_json(silent=True) or {}
        overlay = settings_mod.load_overlay(settings_path)
        ui_payload = {key: payload[key] for key in ("layout", "zoom", "fit") if key in payload}
        try:
            ui_update = _ui_update(ui_payload) if ui_payload else {}
        except ValueError as exc:
            return jsonify({"error": "bad %s" % exc}), 400
        if "refresh_minutes" in payload:
            value = payload.get("refresh_minutes")
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1440:
                return jsonify({"error": "refresh_minutes must be an integer 0..1440"}), 400
            overlay["refresh_minutes"] = value
        if "due_soon_days" in payload:
            value = payload.get("due_soon_days")
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 60:
                return jsonify({"error": "due_soon_days must be an integer 0..60"}), 400
            overlay["due_soon_days"] = value
        if "theme" in payload:
            value = payload.get("theme")
            if value not in settings_mod.THEMES:
                return jsonify({"error": "theme must be system, light or dark"}), 400
            overlay["theme"] = value
        if "auto_reload_pages" in payload:
            value = payload.get("auto_reload_pages")
            if not isinstance(value, bool):
                return jsonify({"error": "auto_reload_pages must be true or false"}), 400
            overlay["auto_reload_pages"] = value
        if "vault" in payload:
            value = _clean_path(payload.get("vault"))
            if not value:
                overlay.pop("vault", None)
            elif not notes.lp(value).is_dir():
                return jsonify({"error": "vault must be an existing folder"}), 400
            else:
                overlay["vault"] = value
        if "personal_tasks_path" in payload:
            value = _clean_path(payload.get("personal_tasks_path"))
            if not value:
                overlay.pop("personal_tasks_path", None)
            elif not value.lower().endswith(".md") or not notes.lp(Path(value).parent).is_dir():
                return jsonify({"error": "personal_tasks_path must be a .md file in an existing folder"}), 400
            else:
                overlay["personal_tasks_path"] = value
        if "time_estimate_template" in payload:
            value = _clean_path(payload.get("time_estimate_template"))
            if not value:
                overlay.pop("time_estimate_template", None)
            elif not notes.lp(value).is_file():
                return jsonify({"error": "time_estimate_template must be an existing file"}), 400
            else:
                overlay["time_estimate_template"] = value
        if "ape_sheet_path" in payload:
            value = _clean_path(payload.get("ape_sheet_path"))
            if not value:
                overlay.pop("ape_sheet_path", None)
            elif notes.lp(value).is_dir() or (
                notes.lp(value).is_file() and value.lower().endswith(".xlsx")
            ):
                overlay["ape_sheet_path"] = value
            else:
                return jsonify({"error": "ape_sheet_path must be an existing .xlsx file or folder"}), 400
        settings_mod.save_overlay(settings_path, overlay)
        before_vault = live["vault"]
        new_cfg = _load_config(config_path)
        cfg.clear()
        cfg.update(new_cfg)
        apply_settings()
        if ui_update:
            def apply(state: dict) -> dict:
                state["ui"] = {**_ui_prefs(state.get("ui") or {}), **ui_update}
                return state["ui"]

            mutate_state(apply)
        rerun = False
        if live["vault"] != before_vault:
            start_run()
            rerun = True
        return jsonify({"ok": True, "settings": effective_settings(), "rerun": rerun})

    if not no_refresh:
        threading.Thread(target=refresh_loop, daemon=True).start()

    return app
