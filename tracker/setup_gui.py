from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
from datetime import date
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONFIG_PATH = HERE / "config.yaml"
EXAMPLE_PATH = HERE / "config.example.yaml"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tracker import rules as rules_mod  # noqa: E402
from tracker import setup_wizard  # noqa: E402
from tracker.matcher import LAYOUT_CUSTOMER, LAYOUTS  # noqa: E402
from tracker.sources import resolve_folder  # noqa: E402


def load_base(target: Path = CONFIG_PATH, detect=setup_wizard.detect_defaults) -> tuple[dict, bool]:
    """Example config overlaid with the existing config; blanks filled from detected defaults."""
    existing = target.is_file()
    cfg = setup_wizard.load_base(target)
    return (cfg if existing else personalize(cfg, detect())), existing


def personalize(cfg: dict, detected: dict) -> dict:
    cfg = dict(cfg)
    for key in ("vault", "personal_tasks_path", "time_estimate_template"):
        if not cfg.get(key) and detected.get(key):
            cfg[key] = setup_wizard._fwd(detected[key])
    me = dict(cfg.get("me") or {})
    for key in ("name", "email"):
        me[key] = me.get(key) or detected.get(key) or ""
    cfg["me"] = me
    ape = dict(cfg.get("ape_sheet") or {})
    ape["path"] = ape.get("path") or (Path.home() / "Downloads").as_posix()
    ape["my_initials"] = ape.get("my_initials") or detected.get("initials") or ""
    cfg["ape_sheet"] = ape
    return cfg


def _csv(items) -> str:
    return ", ".join(str(item) for item in items or [])


SCOPE_LABELS = {"mailbox": "Whole mailbox", "folders": "Specific folders"}


def _rules_path(cfg: dict) -> Path:
    path = Path(cfg.get("rules_path") or (HERE / "rules.yaml"))
    return path if path.is_file() else HERE / "rules.yaml"


def default_rules(cfg: dict) -> dict:
    return rules_mod.load_rules(_rules_path(cfg))


def email_rule_rows(triggers: list[dict]) -> list[dict]:
    return [
        {
            "id": str(t.get("id") or ""),
            "label": str(t.get("label") or t.get("id") or ""),
            "stage": str(t.get("stage") or ""),
            "keywords": ", ".join(rules_mod.split_keywords(t.get("keywords"))),
        }
        for t in triggers or []
        if t.get("id")
    ]


def form_from_config(cfg: dict) -> dict:
    outlook = cfg.get("outlook_com") or {}
    ape = cfg.get("ape_sheet") or {}
    hub = cfg.get("hub") or {}
    me = cfg.get("me") or {}
    return {
        "name": str(me.get("name") or ""),
        "email": str(me.get("email") or ""),
        "wire_diagram_builder": str((cfg.get("tools") or {}).get("wire_diagram_builder") or ""),
        "initials": str(ape.get("my_initials") or ""),
        "vault": str(cfg.get("vault") or ""),
        "folder_layout": str(cfg.get("project_folder_layout") or LAYOUT_CUSTOMER),
        "personal_tasks_path": str(cfg.get("personal_tasks_path") or ""),
        "time_estimate_template": str(cfg.get("time_estimate_template") or ""),
        "templates_root": str(cfg.get("templates_root") or ""),
        "eml_folders": [str(item) for item in cfg.get("eml_folders") or []],
        "scan_project_folders": bool(cfg.get("scan_project_folders", False)),
        "outlook_enabled": bool(outlook.get("enabled", False)),
        "outlook_folders": _csv(outlook.get("folders") or ["Inbox"]),
        "outlook_scope": SCOPE_LABELS.get(str(outlook.get("scope") or "mailbox").lower(), SCOPE_LABELS["mailbox"]),
        "skip_folders": _csv(outlook.get("skip_folders")),
        "email_rules": email_rule_rows(rules_mod.load_rules(_rules_path(cfg), cfg)["triggers"]),
        "buckets": [str(b) for b in default_rules(cfg).get("buckets") or []],
        "outlook_calendar": bool(outlook.get("calendar", False)),
        "days_back": str(outlook.get("days_back") or 120),
        "ape_enabled": bool(ape.get("enabled", True)),
        "ape_auto_find": bool(ape.get("auto_find", True)),
        "ape_path": str(ape.get("path") or ""),
        "ape_url": str(ape.get("url") or ""),
        "exclude_folders": _csv(cfg.get("exclude_folders")),
        "auto_create_projects": bool(cfg.get("auto_create_projects", True)),
        "due_soon_days": str(cfg.get("due_soon_days") if cfg.get("due_soon_days") is not None else 3),
        "hub_port": str(hub.get("port") or 8765),
        "refresh_minutes": str(hub.get("refresh_minutes") if hub.get("refresh_minutes") is not None else 15),
        "toast": bool(hub.get("toast", True)),
        "holidays": "\n".join(str(item) for item in cfg.get("holidays") or []),
        "pm_schedule_include": str(cfg.get("pm_schedule_include") or ""),
        "install_dashboard_note": bool(cfg.get("install_dashboard_note", False)),
    }


def _path(value: str) -> str:
    value = value.strip().strip('"')
    return Path(value).as_posix() if value else ""


def _int(value: str, label: str, errors: list[str], low: int, high: int) -> int | None:
    try:
        number = int(str(value).strip())
    except ValueError:
        errors.append("%s must be a whole number." % label)
        return None
    if not low <= number <= high:
        errors.append("%s must be between %d and %d." % (label, low, high))
        return None
    return number


def _split(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,\n]", value or "") if item.strip()]


def build_config(form: dict, base: dict) -> tuple[dict, list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    cfg = dict(base)

    name = form["name"].strip()
    if not name:
        warnings.append("Your name is empty: PM schedule rows assigned to you will not be recognised.")
    me = dict(cfg.get("me") or {})
    me["name"] = name
    me["email"] = form.get("email", me.get("email") or "").strip()
    cfg["me"] = me

    tool = _path(form.get("wire_diagram_builder", ""))
    if tool and not Path(tool).is_file():
        errors.append("WireDiagramBuilder.exe not found: %s" % tool)
    tools = dict(cfg.get("tools") or {})
    tools["wire_diagram_builder"] = tool
    cfg["tools"] = tools

    vault = _path(form["vault"])
    if not vault:
        errors.append("Project folder (vault) is required.")
    elif not Path(vault).is_dir():
        errors.append("Project folder (vault) does not exist: %s" % vault)
    cfg["vault"] = vault

    layout = str(form.get("folder_layout") or LAYOUT_CUSTOMER).strip().lower()
    if layout not in LAYOUTS:
        errors.append("Project folder layout must be one of: %s" % ", ".join(LAYOUTS))
    cfg["project_folder_layout"] = layout

    tasks = _path(form["personal_tasks_path"])
    if tasks:
        if not tasks.lower().endswith(".md"):
            errors.append("Personal tasks file must be a .md file.")
        elif not Path(tasks).parent.is_dir():
            errors.append("Folder for the personal tasks file does not exist: %s" % Path(tasks).parent.as_posix())
        cfg["personal_tasks_path"] = tasks
    elif vault:
        cfg["personal_tasks_path"] = vault + "/My Tasks.md"

    templates_root = _path(form.get("templates_root", ""))
    has_project_files = isinstance(cfg.get("project_files"), dict)
    if templates_root and not Path(templates_root).is_dir():
        warnings.append("Template folder not found (template files will be skipped until it is reachable): %s" % templates_root)
    elif not templates_root and has_project_files:
        warnings.append("No template folder: new projects will not get template files.")
    cfg["templates_root"] = templates_root

    template = _path(form["time_estimate_template"])
    if template and not Path(template).is_file():
        errors.append("Time estimate template not found: %s" % template)
    elif not template and not has_project_files:
        warnings.append("No time estimate template: new projects will not get a TimeEstimates.xlsx copy.")
    cfg["time_estimate_template"] = template

    folders = [_path(item) for item in form["eml_folders"] if _path(item)]
    missing = [item for item in folders if not resolve_folder(item).is_dir() and Path(item).name != "email_drop"]
    if missing:
        warnings.append("These email folders do not exist and will be skipped:\n  " + "\n  ".join(missing))
    cfg["eml_folders"] = folders
    cfg["scan_project_folders"] = bool(form["scan_project_folders"])

    outlook = dict(cfg.get("outlook_com") or {})
    outlook["enabled"] = bool(form["outlook_enabled"])
    outlook["folders"] = _split(form["outlook_folders"]) or ["Inbox"]
    scope = {label.lower(): key for key, label in SCOPE_LABELS.items()}.get(
        str(form.get("outlook_scope") or "").strip().lower(), str(form.get("outlook_scope") or "mailbox").strip().lower()
    )
    if scope not in SCOPE_LABELS:
        errors.append("Outlook search must be Whole mailbox or Specific folders.")
        scope = "mailbox"
    outlook["scope"] = scope
    outlook["skip_folders"] = _split(form.get("skip_folders", ""))
    outlook["calendar"] = bool(form["outlook_calendar"])
    days = _int(form["days_back"], "Days of email history", errors, 1, 3650)
    if days is not None:
        outlook["days_back"] = days
    cfg["outlook_com"] = outlook
    if not outlook["enabled"] and not folders and not cfg["scan_project_folders"]:
        warnings.append("No email source is enabled: the tracker will not find any assignment emails.")

    if "email_rules" in form:
        defaults = default_rules(cfg)
        buckets = [str(b) for b in defaults.get("buckets") or []]
        rows = []
        for row in form["email_rules"]:
            keywords = rules_mod.split_keywords(row.get("keywords"))
            stage = str(row.get("stage") or "").strip()
            if buckets and stage not in buckets:
                errors.append("Email rule \"%s\": choose a bucket (%s)." % (row.get("label") or row.get("id"), ", ".join(buckets)))
            if not keywords:
                warnings.append("Email rule \"%s\" has no keywords: those emails will not move projects." % (row.get("label") or row.get("id")))
            rows.append({"id": row["id"], "label": row.get("label") or row["id"], "stage": stage, "keywords": keywords})
        if rows == [dict(r, keywords=rules_mod.split_keywords(r["keywords"])) for r in email_rule_rows(defaults["triggers"])]:
            cfg.pop("email_rules", None)
        else:
            cfg["email_rules"] = rows

    ape = dict(cfg.get("ape_sheet") or {})
    ape["enabled"] = bool(form["ape_enabled"])
    ape["auto_find"] = bool(form["ape_auto_find"])
    ape["path"] = _path(form["ape_path"])
    ape["url"] = form["ape_url"].strip()
    initials = form["initials"].strip().upper()
    ape["my_initials"] = initials
    if ape["enabled"]:
        if not initials:
            warnings.append("Initials are empty: new assignments in the APE sheet will not be detected.")
        if ape["path"] and not Path(ape["path"]).exists():
            warnings.append("APE sheet folder does not exist: %s" % ape["path"])
    cfg["ape_sheet"] = ape

    cfg["exclude_folders"] = _split(form["exclude_folders"])
    cfg["auto_create_projects"] = bool(form["auto_create_projects"])
    due = _int(form["due_soon_days"], "Due soon days", errors, 0, 365)
    if due is not None:
        cfg["due_soon_days"] = due

    hub = dict(cfg.get("hub") or {})
    port = _int(form["hub_port"], "Hub port", errors, 1024, 65535)
    if port is not None:
        hub["port"] = port
    refresh = _int(form["refresh_minutes"], "Refresh minutes", errors, 0, 1440)
    if refresh is not None:
        hub["refresh_minutes"] = refresh
    hub["toast"] = bool(form["toast"])
    cfg["hub"] = hub

    holidays = []
    for item in _split(form["holidays"]):
        try:
            holidays.append(date.fromisoformat(item))
        except ValueError:
            errors.append("Holiday is not a YYYY-MM-DD date: %s" % item)
    cfg["holidays"] = sorted(set(holidays))

    pattern = form["pm_schedule_include"].strip()
    try:
        re.compile(pattern)
    except re.error as exc:
        errors.append("PM schedule pattern is not a valid regex: %s" % exc)
    cfg["pm_schedule_include"] = pattern
    cfg["install_dashboard_note"] = bool(form["install_dashboard_note"])
    return cfg, errors, warnings


def write_config(cfg: dict, path: Path = CONFIG_PATH) -> Path | None:
    backup = None
    if path.is_file():
        backup = path.with_suffix(".yaml.bak")
        shutil.copy2(path, backup)
    text = yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True, default_flow_style=False, width=1000)
    header = "# Written by the setup GUI (tracker/setup_gui.py). See config.example.yaml for what each setting does.\n"
    path.write_text(header + text, encoding="utf-8")
    return backup


def run_ui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    base, existing = load_base()
    form = form_from_config(base)

    root = tk.Tk()
    root.title("ProjectHub Setup")
    root.geometry("820x760")
    root.minsize(700, 500)

    outer = ttk.Frame(root)
    outer.pack(fill="both", expand=True)
    canvas = tk.Canvas(outer, highlightthickness=0)
    scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    body = ttk.Frame(canvas, padding=12)
    body.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
    window = canvas.create_window((0, 0), window=body, anchor="nw")
    canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")
    root.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

    variables: dict[str, tk.Variable] = {}
    texts: dict[str, tk.Text] = {}

    intro = (
        "Editing your existing tracker/config.yaml." if existing
        else "No tracker/config.yaml yet: starting from config.example.yaml."
    )
    ttk.Label(body, text="ProjectHub setup", font=("Segoe UI", 14, "bold")).pack(anchor="w")
    ttk.Label(body, text=intro, foreground="gray").pack(anchor="w", pady=(0, 8))

    def section(title: str, parent=None) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent or body, text=title, padding=10)
        frame.pack(fill="x", pady=6)
        frame.columnconfigure(1, weight=1)
        return frame

    def hint(frame, row: int, text: str) -> None:
        ttk.Label(frame, text=text, foreground="gray", wraplength=640, justify="left").grid(
            row=row, column=1, columnspan=2, sticky="w", pady=(0, 6)
        )

    def entry(frame, row: int, key: str, label: str, help_text: str = "", browse: str = "") -> int:
        variables[key] = tk.StringVar(value=form[key])
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(frame, textvariable=variables[key]).grid(row=row, column=1, sticky="ew")
        if browse:
            ttk.Button(frame, text="Browse...", command=lambda: pick(key, browse)).grid(row=row, column=2, padx=(6, 0))
        if help_text:
            hint(frame, row + 1, help_text)
        return row + 2

    def check(frame, row: int, key: str, label: str, help_text: str = "") -> int:
        variables[key] = tk.BooleanVar(value=form[key])
        ttk.Checkbutton(frame, text=label, variable=variables[key]).grid(row=row, column=0, columnspan=3, sticky="w")
        if help_text:
            hint(frame, row + 1, help_text)
        return row + 2

    def start_dir(key: str) -> str:
        current = variables[key].get().strip()
        for candidate in (current, os.path.dirname(current), variables["vault"].get().strip()):
            if candidate and os.path.isdir(candidate):
                return candidate
        return str(Path.home())

    def pick(key: str, kind: str) -> None:
        if kind == "dir":
            value = filedialog.askdirectory(parent=root, initialdir=start_dir(key))
        elif kind == "md":
            value = filedialog.asksaveasfilename(
                parent=root, initialdir=start_dir(key), initialfile="My Tasks.md",
                defaultextension=".md", filetypes=[("Markdown", "*.md")], confirmoverwrite=False,
            )
        else:
            types = [("Programs", "*.exe")] if kind == "exe" else [("Excel", "*.xlsx")]
            value = filedialog.askopenfilename(
                parent=root, initialdir=start_dir(key), filetypes=types + [("All files", "*.*")]
            )
        if value:
            variables[key].set(Path(value).as_posix())

    you = section("1. About you")
    row = entry(you, 0, "name", "Your name", "Exactly as it appears in PM kickoff schedules.")
    row = entry(you, row, "email", "Work email")
    entry(you, row, "initials", "Initials", "As used in the APE Project Tracking sheet's assignment columns.")

    folders = section("2. Project files")
    row = entry(
        folders, 0, "vault", "Project folder (vault)",
        "Required. The folder that holds one subfolder per project (each with a .md project note).", "dir",
    )
    variables["folder_layout"] = tk.StringVar(value=form["folder_layout"])
    ttk.Label(folders, text="New project folders").grid(row=row, column=0, sticky="w", padx=(0, 8))
    ttk.Combobox(
        folders, textvariable=variables["folder_layout"], values=list(LAYOUTS), state="readonly", width=14
    ).grid(row=row, column=1, sticky="w")
    hint(
        folders, row + 1,
        "customer/so: <vault>/Medimmune/SO-295546.   flat: <vault>/Medimmune SO-295546 0207846.\n"
        "Existing folders in either layout are always recognised.",
    )
    row += 2
    row = entry(
        folders, row, "personal_tasks_path", "Personal tasks file",
        "Markdown file for non-project tasks; created if missing. Blank = <vault>/My Tasks.md.", "md",
    )
    row = entry(
        folders, row, "templates_root", "Template folder",
        "Folder with the project templates (TimeEstimates, Parts Request, test matrix, ...), e.g. R:/APE_AE/TEMPLATES.\n"
        "Which files are copied is set under project_files in config.yaml.", "dir",
    )
    row = entry(
        folders, row, "time_estimate_template", "TimeEstimates template (legacy)",
        "Only used when config.yaml has no project_files section.", "xlsx",
    )
    entry(
        folders, row, "wire_diagram_builder", "WireDiagramBuilder.exe",
        "Optional. Launched from wiring-diagram tasks in the hub.", "exe",
    )

    mail = section("3. Email sources")
    ttk.Label(mail, text="Saved-email folders").grid(row=0, column=0, sticky="nw", padx=(0, 8))
    email_list = tk.Listbox(mail, height=4)
    email_list.grid(row=0, column=1, sticky="ew")
    for item in form["eml_folders"]:
        email_list.insert("end", item)
    buttons = ttk.Frame(mail)
    buttons.grid(row=0, column=2, sticky="n", padx=(6, 0))

    def add_email_folder() -> None:
        value = filedialog.askdirectory(parent=root, initialdir=str(ROOT))
        if value:
            email_list.insert("end", Path(value).as_posix())

    def remove_email_folder() -> None:
        for index in reversed(email_list.curselection()):
            email_list.delete(index)

    ttk.Button(buttons, text="Add...", command=add_email_folder).pack(fill="x")
    ttk.Button(buttons, text="Remove", command=remove_email_folder).pack(fill="x", pady=(4, 0))
    hint(mail, 1, "Optional. Folders searched for saved .eml/.msg files (e.g. emails dragged out of Outlook).")
    row = check(mail, 2, "scan_project_folders", "Also search the project folders for saved emails")
    row = check(
        mail, row, "outlook_enabled", "Read classic Outlook (read-only)",
        "Requires classic Outlook (not \"New Outlook\") signed in on this PC.",
    )
    variables["outlook_scope"] = tk.StringVar(value=form["outlook_scope"])
    ttk.Label(mail, text="Outlook search").grid(row=row, column=0, sticky="w", padx=(0, 8))
    ttk.Combobox(
        mail, textvariable=variables["outlook_scope"], values=list(SCOPE_LABELS.values()), state="readonly", width=18
    ).grid(row=row, column=1, sticky="w")
    hint(
        mail, row + 1,
        "Whole mailbox: every mail folder incl. Sent Items (Junk, Deleted Items, Drafts and Outbox are always skipped).\n"
        "Specific folders: only the Outlook folders listed below.",
    )
    row += 2
    row = entry(
        mail, row, "skip_folders", "Skip folders",
        "Whole mailbox only. Comma-separated folder names or paths to skip, e.g. Graymail, Inbox/IT Announcements.",
    )
    row = entry(mail, row, "outlook_folders", "Outlook folders", "Specific folders only. Comma-separated, e.g. Inbox, Inbox/Projects")
    row = check(mail, row, "outlook_calendar", "Read Outlook calendar (kickoff and other meetings)")
    entry(mail, row, "days_back", "Days of history", "How many days back emails and calendar items are read.")

    rules_frame = section("4. Email rules (which emails move a project to which bucket)")
    ttk.Label(rules_frame, text="Email").grid(row=0, column=0, sticky="w", padx=(0, 8))
    ttk.Label(rules_frame, text="Bucket").grid(row=0, column=1, sticky="w")
    ttk.Label(rules_frame, text="Subject keywords (comma-separated)").grid(row=0, column=2, sticky="w", padx=(6, 0))
    rules_frame.columnconfigure(2, weight=1)
    rule_rows = form["email_rules"]
    for index, rule in enumerate(rule_rows, start=1):
        variables["rule_stage:" + rule["id"]] = tk.StringVar(value=rule["stage"])
        variables["rule_keywords:" + rule["id"]] = tk.StringVar(value=rule["keywords"])
        ttk.Label(rules_frame, text=rule["label"]).grid(row=index, column=0, sticky="w", padx=(0, 8))
        ttk.Combobox(
            rules_frame, textvariable=variables["rule_stage:" + rule["id"]], values=form["buckets"], state="readonly", width=16
        ).grid(row=index, column=1, sticky="w")
        ttk.Entry(rules_frame, textvariable=variables["rule_keywords:" + rule["id"]]).grid(
            row=index, column=2, sticky="ew", padx=(6, 0)
        )
    hint(
        rules_frame, len(rule_rows) + 1,
        "Case and spacing are ignored; replies and forwards don't count. If a subject matches several rules, the later\n"
        "bucket wins. A bucket you set by hand on a project page always wins over emails. Completion is set by hand.",
    )

    ape = section("5. APE Project Tracking sheet")
    row = check(ape, 0, "ape_enabled", "Use the APE Project Tracking workbook (read-only)")
    row = check(
        ape, row, "ape_auto_find", "Find a OneDrive-synced copy automatically",
        "Looks in your OneDrive folders for \"APE Project Tracking.xlsx\".",
    )
    row = entry(
        ape, row, "ape_path", "Fallback folder",
        "Where downloaded copies (APE Project Tracking*.xlsx) are looked for, e.g. Downloads.", "dir",
    )
    entry(ape, row, "ape_url", "Sheet URL", "SharePoint link shown in the hub.")

    toggle_text = tk.StringVar(value="Show advanced settings")
    advanced_holder = ttk.Frame(body)
    toggle = ttk.Button(body, textvariable=toggle_text)
    toggle.pack(anchor="w", pady=(6, 0))
    advanced_holder.pack(fill="x")
    advanced = ttk.LabelFrame(advanced_holder, text="Advanced", padding=10)
    advanced.columnconfigure(1, weight=1)

    def toggle_advanced() -> None:
        if advanced.winfo_ismapped():
            advanced.pack_forget()
            toggle_text.set("Show advanced settings")
        else:
            advanced.pack(fill="x", pady=6)
            toggle_text.set("Hide advanced settings")

    toggle.configure(command=toggle_advanced)
    row = entry(advanced, 0, "exclude_folders", "Excluded folders", "Comma-separated vault subfolders that are not projects.")
    row = check(
        advanced, row, "auto_create_projects", "Create project folders automatically from assignment emails",
        "Off: new assignments wait in the hub for you to click Create project.",
    )
    row = entry(advanced, row, "due_soon_days", "Due soon (days)", "Tracker summary threshold.")
    row = entry(advanced, row, "hub_port", "Hub port", "The hub runs at http://localhost:<port>.")
    row = entry(advanced, row, "refresh_minutes", "Hub refresh (minutes)", "How often the hub re-reads email. 0 = never.")
    row = check(advanced, row, "toast", "Windows notification for new assignments")
    row = check(advanced, row, "install_dashboard_note", "Copy Dashboard.md into the vault (Obsidian users)")
    row = entry(advanced, row, "pm_schedule_include", "PM schedule pattern", "Regex of extra PM schedule rows to track.")
    ttk.Label(advanced, text="Holidays").grid(row=row, column=0, sticky="nw", padx=(0, 8))
    holidays = tk.Text(advanced, height=6, width=20)
    holidays.insert("1.0", form["holidays"])
    holidays.grid(row=row, column=1, sticky="w")
    texts["holidays"] = holidays
    hint(advanced, row + 1, "One YYYY-MM-DD date per line. Skipped when counting business-day deadlines.")

    log = tk.Text(body, height=12, wrap="word", state="disabled", background="#f7f7f7")

    def write_log(text: str) -> None:
        log.configure(state="normal")
        log.insert("end", text)
        log.see("end")
        log.configure(state="disabled")

    def current_form() -> dict:
        values = {key: var.get() for key, var in variables.items()}
        values["eml_folders"] = list(email_list.get(0, "end"))
        values["email_rules"] = [
            {
                "id": rule["id"],
                "label": rule["label"],
                "stage": variables["rule_stage:" + rule["id"]].get(),
                "keywords": variables["rule_keywords:" + rule["id"]].get(),
            }
            for rule in rule_rows
        ]
        values["holidays"] = texts["holidays"].get("1.0", "end")
        return values

    def save() -> bool:
        cfg, errors, warnings = build_config(current_form(), base)
        if errors:
            messagebox.showerror("Please fix these first", "\n\n".join(errors), parent=root)
            return False
        if warnings and not messagebox.askyesno(
            "Save anyway?", "\n\n".join(warnings) + "\n\nSave anyway?", parent=root
        ):
            return False
        backup = write_config(cfg)
        base.clear()
        base.update(cfg)
        setup_wizard.ensure_files(cfg)
        answers = {key: cfg.get(key) for key in setup_wizard.OVERLAY_KEYS}
        removed = setup_wizard.clean_settings(CONFIG_PATH, answers)
        write_log("Saved %s\n" % CONFIG_PATH)
        if backup:
            write_log("Previous version kept as %s\n" % backup.name)
        if removed:
            write_log("Removed hub Settings overrides that would replace these paths: %s\n" % ", ".join(removed))
        return True

    def run_command(args: list[str], title: str) -> None:
        write_log("\n> %s\n" % title)

        def worker() -> None:
            try:
                proc = subprocess.Popen(
                    args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                    encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                for line in proc.stdout:
                    root.after(0, write_log, line)
                code = proc.wait()
                root.after(0, write_log, "(exit code %d)\n" % code)
            except OSError as exc:
                root.after(0, write_log, "Failed: %s\n" % exc)

        threading.Thread(target=worker, daemon=True).start()

    def test_run() -> None:
        if save():
            run_command(
                [sys.executable, "-m", "tracker.tracker", "--dry-run", "--config", str(CONFIG_PATH)],
                "Test run (dry run, writes nothing)",
            )

    actions = ttk.Frame(body)
    actions.pack(fill="x", pady=(10, 6))
    ttk.Button(actions, text="Save", command=save).pack(side="left")
    ttk.Button(actions, text="Save and test run", command=test_run).pack(side="left", padx=6)
    ttk.Button(actions, text="Close", command=root.destroy).pack(side="right")
    log.pack(fill="both", expand=True)

    root.mainloop()


if __name__ == "__main__":
    run_ui()
    if not CONFIG_PATH.is_file():
        print("Setup closed without saving tracker/config.yaml.")
        sys.exit(1)
