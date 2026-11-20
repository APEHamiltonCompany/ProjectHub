# Project Hub: How It Works

This document explains how the dashboard is built, started and kept up to date. For setup steps and day-to-day usage see `USER_GUIDE.md`.

## 1. The big picture

ProjectHub is two cooperating programs that share files on disk. There is no database and no cloud service.

| Part | Entry point | Job |
|---|---|---|
| **Tracker** (batch job) | `python -m tracker.tracker` | Reads emails and calendar, works out deadlines, writes tasks into each project's `.md` note, and writes `output/snapshot.json`. |
| **Hub** (web server) | `python -m tracker.hub` | Local Flask web app. Reads `snapshot.json` plus the notes and renders the dashboard in your browser. Runs the tracker on a timer and on demand. |

```
 Outlook (classic, COM, read-only)   saved .eml/.msg files   APE tracking .xlsx (read-only copy)
                 \                         |                       /
                  +--------------->  TRACKER run  <---------------+
                                       |      |
              project notes (.md)  <----+      +---->  output/snapshot.json, tracker/state.json
              (tasks added/updated)                          |
                         ^                                   v
                         |                           HUB (Flask, 127.0.0.1:<port>)
                         +-- your ticks/edits  <----   renders HTML  ---->  your browser
```

The tracker never ticks or closes tasks. Only your own `[x]` (done) or `[-]` (not needed) marks count.

## 2. How the dashboard is displayed (framework)

- **Flask** (Python) serves everything. `tracker/hub/app.py` defines the routes.
- **Jinja2 templates** (`tracker/hub/templates/`) build the HTML on the server. There is no React/Vue/Angular and no build step.
- **Plain JavaScript** (`tracker/hub/static/hub.js`) handles clicks, dropdowns, popups and `fetch()` calls to the hub's JSON API. **CSS** is in `static/hub.css`.
- **Plotly.js** draws the Gantt chart used by the Timeline view. Flask serves it from the installed `plotly` Python package at `/vendor/plotly.min.js`, so no internet is needed.

### How Board / Timeline / Focus are generated
All three are the same page (`GET /`, handler `dashboard()` in `app.py`, template `dashboard.html`). The hub:

1. Loads `output/snapshot.json` (projects, tasks, pending assignments, unmatched emails) and `tracker/state.json`.
2. Computes the open tasks, KPIs, project cards, board columns, Focus buckets and Gantt rows.
3. Picks one partial template based on the chosen layout:

| Layout | Template | What it shows |
|---|---|---|
| Board | `_board.html` | Project cards in one column per workflow stage (stages come from `tracker/rules.yaml`). |
| Timeline | `_timeline.html` / `_gantt.html` | Gantt chart drawn in the browser by Plotly from `/api/gantt` data. |
| Focus | `_focus.html` | Open tasks grouped into time buckets (overdue, due soon, upcoming, done this week) plus personal tasks. |

The KPI strip (`_kpis.html`) and pending-assignment panel (`_pending.html`) appear above all layouts. The chosen layout is stored in `state.json` (`ui.layout`) so it survives restarts. A `?layout=` query parameter overrides it for that request.

## 3. Libraries and resources

`requirements.txt`:

| Package | Used for |
|---|---|
| `flask` | Web server and routing, Jinja2 templating |
| `plotly` | Gantt chart and the bundled `plotly.min.js` |
| `pyyaml` | Reading `config.yaml`, `rules.yaml` |
| `openpyxl` | Reading the APE Project Tracking workbook |
| `extract-msg` | Reading saved `.msg` emails |
| `pywin32` | Classic Outlook COM access, Windows integration |
| `tzlocal` | Local time zone handling |

Other resources: Python 3.10+, Windows (file locking uses `msvcrt`; Outlook, toasts and Task Scheduler are Windows features), classic Outlook (not "New Outlook"), standard-library `tkinter` for the setup window, and optionally Chrome plus the extension in `chrome_extension/`.

Data files:

| File | Purpose |
|---|---|
| `tracker/config.yaml` | Your per-user settings (created from `config.example.yaml` by setup, gitignored) |
| `tracker/settings.json` | Overrides saved from the hub's Settings page; layered over `config.yaml` |
| `tracker/rules.yaml` | Email triggers, workflow steps, stages and buckets |
| `tracker/state.json` | Processed emails, pending assignments, due-date overrides, UI prefs, tick log |
| `output/snapshot.json` | The tracker's latest result, which the dashboard renders |
| `output/outlook_cache.json` | Cache of read mail, for incremental reads |
| `output/hub.log`, `hub_last_run.log`, `last_run.log` | Logs |
| `<vault>/.../<project>.md` | The project notes that hold the tasks. These are the source of truth for ticks. |

## 4. Starting the hub

### Startup sequence (`python -m tracker.hub`)
1. `tracker/hub/__main__.py` parses `--config`, `--port` and `--no-refresh`. If it runs under `pythonw` (no console), output goes to `output/hub.log`.
2. It exits with a message if `tracker/config.yaml` is missing (run `setup.cmd`).
3. `create_app()` loads config plus the `settings.json` overlay, loads `rules.yaml`, creates a random per-process security token, and registers the routes.
4. Unless `--no-refresh` is set, a background thread starts `refresh_loop()`.
5. Flask listens on `127.0.0.1:<port>` (default 8765, from `hub.port` in config or `--port`).

### How users should start it
- **Normal: let setup do it.** `setup.cmd` registers a Windows scheduled task named `ProjectHub` (`install_hub_startup.ps1`) that starts the hub at every logon, hidden, via `pythonw -m tracker.hub`. It also creates a desktop shortcut `Project Hub.url`. After that, just open the shortcut.
- **Manually:** double-click or run `run_hub.cmd` (uses `.venv` if present). The console stays open while the hub runs.
- **Restart the background task:** `Stop-ScheduledTask -TaskName ProjectHub` then `Start-ScheduledTask -TaskName ProjectHub` in PowerShell.
- **Different port:** `run_hub.cmd --port 9000`, or set `hub.port` in `tracker/config.yaml` (the setup window's advanced section has it). Re-run `setup.cmd` so the shortcut matches.

A separate `ProjectTracker` scheduled task (`install_schedule.ps1`) runs the tracker alone on weekdays at 06:30 using `run_tracker.cmd`. It is optional because the hub already runs the tracker itself while it is up.

### Why `localhost:8765`?
- **It is a local app.** The hub reads your mailbox-derived data and edits your notes, so it binds to the loopback address `127.0.0.1`. Other computers cannot connect. `localhost` is the standard name that resolves to that address.
- **8765 is just a default.** It is an unprivileged port unlikely to clash with common tools. It is configurable as described above.
- **"Different hostname" note.** Wherever this document or the guide says `localhost:8765`, read it as `<host>:<port>` where host is `localhost` or `127.0.0.1` and port is your configured `hub.port`. The hub rejects any other `Host` header (HTTP 403) as protection against DNS-rebinding, and it listens only on loopback. So a machine name, a LAN IP or a reverse proxy will not work without code changes in `__main__.py` (bind address) and `allowed_hosts` in `app.py`.

### Security guards (every request)
- The `Host` header must be `localhost:<port>` or `127.0.0.1:<port>`.
- Every `POST` must be JSON, carry the `X-Hub-Token` header (the token is embedded in each page as `<meta name="hub-token">`), and, if it has an `Origin`, that origin must be local. The token changes each time the hub restarts, so open pages from before a restart need a reload.

## 5. What happens at first launch

1. **Setup (once).** `setup.cmd` creates `.venv`, installs requirements, writes `tracker/config.yaml`, registers the scheduled tasks, starts the hub and creates the desktop shortcut.
2. **Hub starts with no snapshot.** `output/snapshot.json` does not exist yet, so `load_snapshot()` returns an empty structure. The page renders but shows no projects, and the sidebar shows "Last refresh: never".
3. **Immediate tracker run.** `refresh_loop()` calls `run_tracker()` straight away on its background thread. That launches `python -m tracker.tracker --config ...` as a subprocess (hidden window, 10 minute limit), and its output is appended to `output/hub_last_run.log`. The tracker:
   - scans the vault and builds the project list; creates a note for any project folder that lacks one;
   - reads emails: saved `.eml/.msg` files, project folders, and classic Outlook mail and calendar for the last `outlook_com.days_back` days (default 60). This is a **full read** and is the slowest step; results are cached in `output/outlook_cache.json`;
   - matches emails to projects, applies the triggers in `rules.yaml` to work out due dates (skipping `holidays`), and detects new assignment emails, which go to the **pending** list (or create folders automatically if `auto_create_projects` is true);
   - loads the APE tracking workbook (downloading a copy if its shortcut is configured and the copy is over `download_hours` old);
   - inserts or updates the tracker-owned task block inside each project note, keeping your marks;
   - writes `tracker/state.json`, appends to `output/last_run.log`, writes `output/dashboard.html` (a static report) and finally `output/snapshot.json`.
4. **Page picks up the result.** The browser polls `/api/status` every 60 seconds; when `generated` changes and the page is idle, it reloads and shows data. A reload by hand also works once the run has finished.
5. If new assignment emails were found, a Windows toast is shown (`hub.toast`).

Opening `/p/<key>` for a project folder that is not in the snapshot yet shows a "loading" page and triggers a run.

## 6. What happens on the 2nd, 3rd, ... launch

The hub itself is stateless between launches. Everything it needs is on disk, so it starts faster and shows data at once.

1. **Instant first paint.** The previous `snapshot.json` and `state.json` are loaded. The dashboard is complete immediately, with the old "Last refresh" time.
2. **A refresh still runs at startup** (unless `--no-refresh`), so data updates shortly after. Pages auto-reload when a newer snapshot appears (if "Auto reload pages" is on in Settings).
3. **Mail reads are incremental.** With `outlook_com.incremental: true` the tracker only reads mail received since the last read (minus one day of overlap) and merges it with `output/outlook_cache.json`. A full read repeats when the last one is older than `full_read_hours` (24), or when the scope, folders or `days_back` changed.
4. **Already-processed emails are remembered** in `state.json` (`processed`, `pending`, overrides), so pending items are not re-announced and your classifications and due-date overrides persist.
5. **Your edits survive.** Marks, manual tasks, stage changes made by hand, the selected layout, zoom and theme are all stored in notes or `state.json`.
6. **Recurring refresh.** After startup, the loop wakes every 5 seconds and runs the tracker again when `refresh_minutes` (default 15) have passed and no run is active. `refresh_minutes: 0` disables the timer, leaving only manual refresh.
7. **Scheduled tasks.** If the logon task is enabled, the hub launches at each login (`MultipleInstances IgnoreNew` prevents duplicates). The 06:30 `ProjectTracker` task updates data before you open the page.

## 7. What happens when you click a control

Every interaction follows the same pattern:

```
click / change  ->  hub.js handler  ->  fetch POST /api/...  (JSON + X-Hub-Token)
                 ->  Flask route validates, edits notes/state under a lock
                 ->  JSON reply  ->  JS updates the element in place, or reloads the page
```

| Control | What happens |
|---|---|
| **Board / Timeline / Focus** buttons | `POST /api/ui {layout}` saves the preference in `state.json`, then the page reloads and `dashboard()` renders the chosen partial. Disabled outside the Dashboard page. |
| **Zoom** dropdown, **Fit columns** | `POST /api/ui`; zoom is applied as CSS on the content area. |
| **Task tick box** (done / not needed) | `POST /api/task/toggle` (or `/api/note/toggle` for note lines). The hub re-reads the note, checks that the line is unchanged (otherwise HTTP 409 and "Note changed elsewhere - reloading"), rewrites the checkbox as `[ ]`, `[x]` or `[-]`, appends to `tick_log` in `state.json`, and shows a popup to log time. Repeating manual tasks spawn the next occurrence. |
| **Stage** dropdown (project page) | `POST /api/projects/stage` sets a manual stage that wins over email-derived stages. "Follow emails" undoes it. |
| **Classify** dropdown on an unmatched email | `POST /api/classify` stores an override in `state.json`, applied on the next refresh. |
| **Existing folder** dropdown / Create project (pending assignment) | `/api/pending/link`, `/api/pending/create`, `/api/projects/create` create or link a project folder and note, then rerun the tracker. |
| **Due date edit** | `POST /api/due_override`; kept in `state.json` and re-applied on every run. |
| **+ New task**, edit, archive, delete | `/api/tasks/*` edit `My Tasks.md` or a project note. |
| **Launch buttons** (open folder, template, link) | `/api/open`, `/api/task/launch`, `/api/projects/files/open` run on the machine (open a file or URL, copy a template with name tokens filled in). |
| **Gantt bar click** (Timeline) | Plotly click event opens the project or task page. |
| **Settings** page | `POST /api/settings` validates paths, saves `tracker/settings.json`, re-reads the config live, and starts a refresh if the vault folder changed. |

Concurrency: note and state writes happen inside an in-process mutex plus a file lock (`tracker/tracker.lock`), so a tracker run and a click cannot corrupt each other. Notes are written with their original line endings and encoding.

## 8. What "Refresh now" does

The button is in the sidebar (`#refresh-now`, `base.html`).

1. `hub.js` disables the button and calls `POST /api/rerun`.
2. `api_rerun()` calls `start_run()`:
   - if no run is active, it starts `run_tracker()` on a background thread;
   - if one is already running, it sets a **queued** flag so exactly one more run starts right after the current one. It does not run two at once.
3. The run is the same tracker subprocess described in section 5, using incremental mail reads. Output goes to `output/hub_last_run.log`.
4. The sidebar text changes to "Refreshing...". The browser polls `GET /api/status` every 2 seconds (the first poll comes after 1.5 s).
5. When `running` becomes false, the page reloads and shows the new "Last refresh" time. New pending assignments trigger a toast.

It does **not** restart the hub, reload `config.yaml` or force a full Outlook read. Settings changes apply live through the Settings page. Config file edits and `rules.yaml` edits need a hub restart. If the tracker fails (non-zero exit), the old snapshot stays in place. Check `output/hub_last_run.log`.

## 9. Troubleshooting pointers

| Symptom | Check |
|---|---|
| Browser can't connect | Is the hub running? Look for `pythonw.exe`, or start `run_hub.cmd`. Confirm the port: `http://127.0.0.1:<port>/api/status`. |
| 403 "forbidden" | You used a non-local hostname, or the page is from before a hub restart. Use `localhost` or `127.0.0.1` and reload. |
| Empty dashboard | First run may still be reading Outlook. Wait, then check `output/hub_last_run.log` and `output/hub.log`. |
| No email data | Classic Outlook must be installed and signed in (not New Outlook). |
| Config or rules change not seen | Restart the `ProjectHub` task. |

## 10. Code map

| Path | Purpose |
|---|---|
| `tracker/hub/__main__.py` | Hub CLI entry point |
| `tracker/hub/app.py` | Flask app: routes, security guard, refresh loop, run management |
| `tracker/hub/tasks.py`, `timesheet.py` | Task parsing and mark editing; timesheet logic |
| `tracker/hub/templates/`, `static/` | Jinja pages and partials; JS and CSS |
| `tracker/hub/winfocus.py` | Brings the launched window to the foreground |
| `tracker/tracker.py` | Tracker batch job |
| `tracker/sources/` | Outlook COM, `.eml/.msg` folders, APE workbook readers |
| `tracker/matcher.py`, `extract.py`, `rules.py` | Email-to-project matching, parsing, rules engine |
| `tracker/notes.py`, `state.py`, `settings.py`, `locking.py` | Note editing, state file, settings overlay, file lock |
| `tracker/setup_gui.py`, `setup_wizard.py`, `setup.ps1` | Setup experience |
| `install_hub_startup.ps1`, `install_schedule.ps1` | Scheduled task installers |
