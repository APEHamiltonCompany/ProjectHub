# Project Tracker and Project Hub: User Guide

Everything runs on your PC. The tracker reads classic Outlook (read-only), works out deadlines from the Applications Workflow, and writes them into each project's `.md` note. The Project Hub is a local web page at `http://localhost:8765` where you see deadlines, create new projects, edit notes and open a project's Chrome tab group.

Ground rules built into the system:
- It never ticks, strikes or closes anything. It only adds unticked `- [ ]` lines. You mark a step `[x]` (done) or `[-]` (not needed for this project), and your marks are kept exactly as you wrote them.
- It doesn't connect to SharePoint. Links shown in the hub are URLs copied from email bodies or ones you added.
- It never sends email and doesn't read Sent Items.
- The hub only listens on your own PC (127.0.0.1). Other computers can't reach it.

---

## Part 1: One-time setup

### 1.1 Run setup.cmd
Double-click `setup.cmd` in the ProjectHub folder (or run it from a terminal). It walks through everything and can be run again at any time to change your answers.

What it does:
1. **Python**: finds Python 3.10 or newer (`py -3.12`, then `py -3`, then `python`). If none is found it offers to install Python 3.12 with winget, or points you at the python.org download page.
2. **Virtual environment**: creates `.venv` in the ProjectHub folder and installs `requirements.txt` into it.
3. **Outlook**: checks that classic Outlook is registered and warns if it isn't.
4. **Configuration**: runs the setup wizard, which asks for your name, work email, initials, project folder root, My Tasks file, time estimate template, an extra email drop folder, the WireDiagramBuilder path and the hub port, then writes `tracker\config.yaml`.
5. **Background services**: optionally registers the **ProjectHub** logon task and the **ProjectTracker** 06:30 weekday task, starts the hub and waits up to 60 seconds for it to answer.
6. **Desktop shortcut**: creates `Project Hub.url` on your desktop.
7. **Chrome extension**: optionally opens the `chrome_extension` folder, copies its path to the clipboard and opens `chrome://extensions`.

Rerun `setup.cmd` to change an answer: the wizard shows your current values in brackets and Enter keeps them. The previous config is saved as `tracker\config.yaml.bak`.

### 1.2 Prerequisites
1. **Python 3.10 or newer** (3.12 recommended). `setup.cmd` installs it if you agree.
2. **Classic Outlook** (OUTLOOK.EXE), not "New Outlook". The tracker reads the Inbox and Calendar through classic Outlook. Keep it signed in. If it's closed when the tracker runs, the tracker tries to start it in the background.
3. **Google Chrome**, for the tab-group extension (optional).
4. The project folder root (the "vault"), for example
   `%OneDrive%\Desktop\Projects\Project Specific Documents`
5. The time-estimate template, for example
   `%OneDrive%\Desktop\Projects\templates\TimeEstimates.xlsx` (optional).

### 1.3 Manual setup
If you would rather not use `setup.cmd`, run this from `<ProjectHub folder>`:
```bash
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy tracker\config.example.yaml tracker\config.yaml
.venv\Scripts\python.exe -m tracker.setup_wizard
```
Then check `tracker\config.yaml`:
- `vault`: the project folder root above.
- `me.name`: your name as it appears in PM schedules. Rows assigned to you in a kickoff schedule are always tracked.
- `holidays`: company holidays. Deadlines are counted in business days and skip these dates.
- `time_estimate_template`: path to `TimeEstimates.xlsx`.
- `auto_create_projects: false`: new assignments wait in the hub for you to click **Create project**.
- `hub.port` (8765), `hub.refresh_minutes` (15), `hub.toast` (true = Windows notification for new assignments).
- `tools.wire_diagram_builder`: path to `WireDiagramBuilder.exe`. The launch shortcuts in `tracker\rules.yaml` refer to it as `tool:wire_diagram_builder`; leave it empty to hide those buttons.

`tracker\config.yaml` is local and not tracked by git; `tracker\config.example.yaml` is the template.

### 1.4 Do a test run
```bash
.venv\Scripts\python.exe -m tracker.tracker --dry-run
```
This prints what would change and writes nothing. If it looks right, do a real run:
```bash
.venv\Scripts\python.exe -m tracker.tracker
```

### 1.5 Start the hub automatically at login
Run this once in PowerShell:
```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "<ProjectHub folder>\install_hub_startup.ps1"
```
This registers the scheduled task **ProjectHub**. It starts the hub hidden (pythonw) each time you log in, and the hub re-reads Outlook every 15 minutes. To start it right away without logging out:
```bash
powershell.exe -NoProfile -Command "Start-ScheduledTask -TaskName ProjectHub"
```
To run the hub in a visible window instead (for troubleshooting), double-click `run_hub.cmd`. Stop the scheduled copy first, because only one copy can use port 8765.

### 1.6 The early-morning tracker run
The scheduled task **ProjectTracker** runs the tracker on weekdays at 06:30, so the dashboard is fresh before the Monday 7 AM review even if the hub isn't running. `setup.cmd` registers it with `install_schedule.ps1`. It and the hub share a lock file, so they never write at the same time.

### 1.7 Install the Chrome extension (optional, recommended)
1. In Chrome, open `chrome://extensions`.
2. Turn on **Developer mode** (top right).
3. Click **Load unpacked** and choose `<ProjectHub folder>\chrome_extension`.
4. Bookmark `http://localhost:8765`.

The extension is version 2.0.0. After updating it, click **Reload** on **Project Hub Tab Groups** in `chrome://extensions`; it re-attaches to the hub tabs you already have open. If it doesn't, press F5 on the hub page.

**Open as tab group** now always ends with a message: "Tab group ... ready" with how many tabs it opened and how many already-open tabs it moved in, or the reason it failed. Loose tabs that are already open (for example from **Open all links**) are moved into the group instead of being skipped, which is why it used to need several tries. Clicking the button again adds any missing links to the existing group.

Without the extension, the **Open all links** button still opens every link as separate tabs. Chrome may ask you to allow pop-ups for localhost the first time.

---

## Part 2: Daily use

### 2.1 Open the hub
Go to `http://localhost:8765`. The left sidebar has the navigation, a list of your projects (dot colour: red = something overdue, amber = due soon, green = on track), the **Layout** switcher and **Refresh now** ("Last refresh" shows when Outlook was last read). The **Layout** buttons are greyed out with "Dashboard only" on project and Monday pages, because layouts only change the Dashboard. The top bar has a **Zoom** select (50–100%, applies to every page and is remembered) and **+ New task**. The browser tab shows the PH icon. Every dashboard layout shows:
- **New assignment banners**: assignments from APEadmin that don't have a project folder yet.
- **KPI tiles**: Overdue, Due soon, Open tasks, Projects, Done this week.
- **Timeline (Gantt)**: one row per project, one bar per task from its start (the email that triggered it) to its due date, coloured by status, with a dashed "Today" line. Click a bar to open that project.
- **Tasks by due date** table: project (with the customer and project # on a second line), SO/QTE, stage, task, due, days left, status. Each row has a checkbox. Use the filter chips (All / Overdue / Due soon / Open / Done), the **All categories** select and the search box.
- **My tasks**: your own tasks that aren't tied to a project, grouped by category.
- **Unclassified emails**: project emails the tracker couldn't place in the workflow (see 2.6).

### 2.1a Choose a layout
Pick one in the sidebar. The hub remembers your choice; you can also add `?layout=board`, `?layout=timeline` or `?layout=focus` to the address.
- **Board**: a JIRA-style board with one column per project stage (Pre-Kickoff, Design, Design Review, Apps Plan, Fabrication/Test, System Test, Closeout, Completion). Each project is a card showing:
  - the customer, SO/QTE and level;
  - a ship-date countdown and a progress bar;
  - overdue and due-soon counts;
  - the next task, with its own checkbox;
  - the team;
  - **Open as tab group**, and an expand button that lists the project's open tasks.
  
  The timeline and the task table sit below the board. On the board, **Fit columns** (top bar) squeezes every column to the window width instead of scrolling sideways; empty stages get narrower columns. The board uses the full window width, and a lower **Zoom** shows more columns at once. With **Fit columns** on, task titles are clamped to two lines - hover a title to read it in full. In dark mode the cards are a lighter grey than the page background.
- **Timeline**: closest to the old dashboard. KPI tiles, a large Gantt chart, the full task table and a project summary table.
- **Focus**: an agenda of what to do next. Open tasks are grouped into Overdue / Today / This week / Next week / Later / No date. Each task has a quick "Set date" control. A compact six-week timeline and the project list sit on the right.

### 2.1b Add your own tasks
Click **+ New task** (top right, on every page) and fill in:
- **Description** (required)
- **Category**: Project, Personal, Admin, Process improvement, Unassigned project or Other. The default is Project on a project page and Personal everywhere else.
- **Due date**
- **Project**: only shown when the category is Project, and then it is required.
- **Repeat**: does not repeat, every day, every weekday, every week, every 2 weeks or every month. For the weekly choices pick the weekday; for monthly the day comes from the due date (today's day if there is no due date). See 2.1d.
- **Notes** (optional)

Where tasks are saved:
- Project tasks go into that project's `.md` note, under a **My tasks** heading just above the tracker section, for example `- [ ] Call vendor about cable 📅 2026-11-20 🆔 m-1a2b3c4d`. Notes are saved on the line below.
- Non-project tasks go into `My Tasks.md` in the project folder root (the path is set by `personal_tasks_path` in `config.yaml`), with an Obsidian tag for the category, for example `- [ ] Enter timesheet #admin 🔁 every week on Friday 📅 2026-11-13 🆔 m-1a2b3c4d`.

Your own tasks show a "manual" tag. They can be edited or deleted from the hub, and they appear in the Gantt chart, the task table, the Monday page and every layout. The tracker never changes them.
**Archive** moves the task to an **Archived** section at the bottom of the note, hidden from every view; restore it from the **Archived (N)** list on the Board or the project page.

### 2.1c Categories
Every task has one category:

| Category | Tag in the file | Where it lives |
|---|---|---|
| Project | none | the project's note |
| Personal | `#personal` | `My Tasks.md` |
| Admin | `#admin` | `My Tasks.md` |
| Process improvement | `#process-improvement` | `My Tasks.md` |
| Unassigned project | `#unassigned-project` | `My Tasks.md` |
| Other | `#other` | `My Tasks.md` |

- A task in a project note is always **Project**; the hub writes no tag for it.
- A line in `My Tasks.md` with no tag counts as **Personal**, so older lines keep working.
- The hub writes the tag for every new non-project task, including `#personal`.
- The **My tasks** column on the board and the **My tasks** panel group by category, with a small heading and a count per group. Each task shows a category chip.
- The tasks table has an **All categories** select next to the filter chips; it combines with the chips and the search box.
- The **Edit** form on a non-project task has a Category select. Project tasks can't be moved to another category from the hub.

### 2.1d Recurring tasks
Set **Repeat** when you add or edit a task. The rule is stored in the line with the Obsidian Tasks syntax `🔁 <rule>`:
- `every day`
- `every weekday` (Monday to Friday)
- `every week on Friday`
- `every 2 weeks on Monday` (2 to 12 weeks)
- `every month on the 15th` (clamped to the last day of short months, so the 31st becomes 28 February)

Example, a weekly timesheet:
`- [ ] Enter timesheet #admin 🔁 every week on Friday 📅 2026-11-13 🆔 m-1a2b3c4d`

- If you set a repeat rule and leave the due date empty, the hub fills in the first matching date on or after today.
- When you tick a recurring task `[x]` or mark it `[-]`, the hub adds a **new open copy directly above it**, with the same description, category, rule and notes, a new id, and the next due date. The next date is always in the future: a weekly task ticked late lands on the next upcoming Friday, not in the past.
- The hub never ticks anything itself. It only adds the new open line because you ticked the old one.
- If an open copy with the same title, rule and due date already exists, it is not added again, so unticking and re-ticking is safe.
- Unticking never deletes the copy that was added.
- Recurring tasks show a `🔁 weekly` (or `daily`, `weekdays`, `every 2 weeks`, `monthly`) badge in lists and in the tasks table; hover it to see the full rule.
- To stop a task repeating, clear **Repeat** in the Edit form.

### 2.1e Task pages for My Tasks
Every personal task (a line in `My Tasks.md` with an `m-` id) has its own page. Click the task title in the **My tasks** column, the tasks table or the Monday page, or open `/t/<id>` directly.

- The first time you open the page the hub creates a small note at `My Tasks/<task title>/<task title>.md` and adds a `[note:: My Tasks/<task title>/<task title>.md]` token to the task line. The token is stripped from the title in lists and is kept when you edit the task, tick it, log time, or let a recurring task spawn its next copy.
- The page mirrors a project page: tick, Edit, Track time and a shortcut button, a Notes panel with Rendered / Live edit / Edit text and the same save-and-conflict check, a Links panel, a Folder panel and a Time panel with the tracked entries.
- Links accept an `https://` address (opens in a new browser tab) or an existing local file or folder path (opens from the hub). Remove a link with its Remove button.
- The folder defaults to the note's own folder. **Change folder…** points it at another existing directory; Set with an empty box resets it.
- **Shortcut** stores a launch target in the note's frontmatter. Once set, a ▶ button appears on the task page and next to the task in the My tasks column.

### 2.2 When a new project is assigned
1. A Windows notification appears ("New project assignment: ...") and a banner shows at the top of the dashboard.
2. Check the details: customer, SO/QTE, project number, level, team, SharePoint link.
3. Optional: click **Preview note** to see the note that will be created.
4. Choose one:
   - **Create project**: edit the folder name if you like, then click. The hub creates the folder in the vault and a note pre-filled with the frontmatter, team, links, the AE Order Review Checklist from the email and the full workflow checklist. It also copies `TimeEstimates.xlsx` in as `<DDMONYYYY>_TimeEstimates.xlsx`.
   - **Link to existing folder**: if you already made the folder, pick it. Empty fields in its note are filled in; nothing you wrote is changed.
   - **Dismiss**: not your project, or ignore it. It won't come back.
5. Due dates for the first steps appear after the automatic refresh that follows. Those steps are: review CPR/SO, ACE questions, and the time estimate (2 business days after assignment).

### 2.3 Work on a project (project page)
Click a project name, or go to `http://localhost:8765/p/<project number>` (for example `/p/0200000`). The page has:
- **Header**: customer, SO/QTE, level, stage, ship date with days left, design need-by date, team.
- **Links**: SharePoint, CPR, RMT and other links found in the project's emails. Use **Add link** for your own (for example the pptx), and remove links you don't need.
- **Open as tab group** (with the extension): creates a Chrome tab group named after the project, holding this page and every link. If the group already exists, it focuses that group instead.
- **Open folder** opens the project folder in Explorer. **Open note in editor** opens the `.md` file in your default editor.
- **Stage stepper and mini timeline**: where the project is, and its tasks on a small Gantt chart.
- **Deadlines**: every step grouped by stage (My tasks, Pre-Fabrication, System Test, Closeout, Completion, PM schedule), with its due date, status and a checkbox. Click **⋯ adjust** on a row to override its date or add a blocker note. Use **By phase / By due date** in the panel header to switch to one list sorted by next due date (open tasks first, undated after, done at the bottom, each row tagged with its phase). The hub remembers your choice.
- **Launch shortcut**: a deadline row can carry a one-click button that opens a local app, file, folder or https link. The default comes from `launch:` on a `workflow:` entry in `tracker/rules.yaml` (for example `launch: 'tool:wire_diagram_builder'` on Review CPR/SO and Wiring diagram, which resolves through `tools.wire_diagram_builder` in `tracker/config.yaml`). Change it for one project under **⋯ adjust** → **Shortcut**: **Set shortcut**, **Use template** or **No shortcut**. The project value is stored as `task_launch:` in the note frontmatter, so you can also edit it in Obsidian. It accepts a `.exe`, any file or folder, or an https link; in Explorer use Shift+right-click → **Copy as path** and paste. The hub waits up to 15 seconds for the launched app's window and brings it to the front.
- **File templates**: a shortcut can also be `template:<name>`, which copies a template file into the project folder under a name built from the project's SO/QTE number and opens the copy. The template file itself is never written, moved or overwritten, and if the target file is already there the hub just opens it instead of copying again. Define the templates in `tracker/config.yaml`:
  ```yaml
  file_templates:
    parts_request:
      source: 'R:\APE_AE\TEMPLATES\Parts Request\PartsRequestSO-XXXXXXX.xlsx'
      name: 'PartsRequest {ident}.xlsx'
      folders: ['{project_folder}']
      fill: {}
  ```
  `source` is the template to copy. `name` is the new filename and takes `{ident}` (first SO, else first QTE, else the project number), `{so}`, `{qte}`, `{project_no}`, `{customer}` and `{date}`; if a token has no value the copy stops with "this project has no ...". `folders` lists where the copy goes: `{project_folder}` is the project folder, any other entry is an absolute path that must already exist. `fill` is reserved for later. `tracker/rules.yaml` uses it on Parts Required for Testing as `launch: 'template:parts_request'`; override it for one project by typing `template:<name>` in **⋯ adjust** → **Shortcut**.
- **Note > Rendered**: the whole note with real checkboxes. Tick a box to mark it done; click **N/A** to mark it not needed. Each change is saved to the `.md` file immediately.
- **Note > Edit text**: the text editor on the left and a live preview on the right. The preview's checkboxes are clickable too. Nothing is written until you click **Save**, and an "Unsaved changes" flag shows until you do. If the file changed somewhere else since you opened it (for example in OneDrive or Notepad), the hub warns you instead of overwriting. You can then reload, or overwrite on purpose.
- **Note > Live edit**: edit the formatted note directly, like Obsidian's live preview. Every line is shown formatted; click a line to see and edit its markdown in place. **Enter** starts a new line (lists and checkboxes continue automatically; press Enter on an empty item to end the list), **Backspace** at the start of a line joins it to the line above, **Tab**/**Shift+Tab** indents, the arrow keys move between lines and **Esc** stops editing. Checkboxes and links work without entering edit mode. It shares the same **Save** button, unsaved flag and conflict warning as Edit text, and the two tabs stay in sync. Open it directly with `?tab=live` on the project URL.
- **Emails** and **Folder files**: this project's emails and the files in its folder.

You can still edit the `.md` in any editor. The tracker only rewrites the section between `<!-- tracker:start -->` and `<!-- tracker:end -->`, and keeps your `[x]`/`[-]` marks and any due dates you changed inside it.

### 2.4 Mark steps done or not needed
- Done: `[x]`. Not needed for this project: `[-]`.
- Use any checkbox in the hub (dashboard, board cards, project page, note, Monday page) plus its **N/A** button, or type the mark in the note.
- A box changes only when you click it.
- The tracker never does this for you, even after you send the related email.

### 2.4a Logging time
Ticking a task opens a small popover next to it:
- **Hours spent (optional)**: type a number or use a quick button (0.25, 0.5, 1, 2, 4). Enter saves; leaving it empty and pressing Enter is the same as **Skip**.
- **Worked on**: day chips for the current week (Monday to Sunday). Today is pre-selected, future days are disabled, and you can toggle any day up to today.
- **Save** writes the time onto the task line, **Skip** closes the popover without writing anything.

The time is stored in the line in Obsidian/Dataview syntax, for example:
`- [x] Send test plan 📅 2026-11-12 🆔 m-1a2b3c4d ⏱ 2h [days:: 2026-11-10, 2026-11-12]`

- A done task with hours shows a `⏱ 2h` chip; click it to reopen the popover with the hours and days already filled in.
- If you tick a recurring task, the page reload that adds the next occurrence waits until you close the popover.
- **SO / QTE** is an optional field in **New task** and in the Edit form. It accepts `SO-123456` or `QTE-123456`; when you type a title that already contains one, it is offered as the placeholder. It is stored as `[ref:: SO-281090]` and shown as a small chip on personal task rows.

**Track time** lets you log hours on a task without completing it. Open tasks show a `⏱ Track` button: click it, type the hours, pick the day (today by default) and press **Add time**. Each entry is appended to the line as `[time:: 2026-11-12=1.5, 2026-11-13=2]`, so the same day can appear more than once and the order is kept. The task keeps its checkbox state. Once a task has entries it shows a `⏱ 3.5h` chip; click the chip to see every entry and remove one with its **×**. When you later tick that task, the completion popover asks for **extra hours** only, because the tracked entries are already counted. The Timesheet counts each entry on its own day, for open and done tasks alike.

### 2.5 Change a due date or record a blocker
On the project page, each open step has a date box and a note box:
- Set a new date to override the computed one, and/or type a note such as "blocked: waiting on ACE".
- Click **Clear** to go back to the computed date.
- Changing the 📅 date directly in the note works too; the tracker keeps your date from then on.

### 2.6 Triage unclassified emails
The **Unclassified emails** section lists emails that mention a project but don't match a workflow trigger. Example: "Acme QTE-123456 Review".
- Click **Kickoff**, **Design review**, **Internal DR**, **WO firm** or **Closeout** to treat the email as that event. Its date then starts the matching deadlines.
- Click **Ignore** to hide it.
- Click a subject to open the email in Outlook (or the `.eml` file). Nothing about the email is changed: it is not marked read, moved or saved.
- The hub refreshes automatically after you choose.

What each event starts, in business days:

| Email/event | Steps due |
|---|---|
| Assignment | Review CPR/SO, ACE questions, time estimate + RMT: +2 |
| Kickoff | Wiring diagram: +1 |
| Internal design review | Test matrix: +1 |
| Design review | Finish Apps Plan: +5 |
| Work order firm | Parts required for testing: +5 |
| Closeout email | Closeout documents / reply: +5 |

Kickoff emails that include a PM schedule table also add a **PM schedule** section. It lists rows assigned to you plus key milestones: test plan, parts for testing, PreFabApps, need-by, ship, closeout.

### 2.7 Settings page
Open **Settings** in the sidebar, or `http://localhost:8765/settings`. Everything here is saved to `tracker\settings.json`, which overrides `tracker\config.yaml` for both the hub and the scheduled tracker run:
- **Auto-refresh interval** in minutes (0 = off): how often the hub re-reads Outlook.
- **Reload open pages after each refresh**: when on, a page you left open reloads itself once a refresh finishes, as long as you aren't typing in it.
- **Theme**: System, Light or Dark.
- **Default zoom, fit columns and layout**: the starting values for a new browser; the top-bar controls still override them per page.
- **Due-soon window** in days: how far ahead counts as "due soon".
- **Project notes folder**: the `.md` vault. It must already exist. Changing it triggers a refresh so the project list is rebuilt.
- **Personal tasks file**: the `.md` file that holds non-project tasks.
- **TimeEstimates template**: the `.xlsx` copied into a new project folder.

### 2.8 Projects page (archive, delete, restore)
Open **Projects** in the sidebar, or `http://localhost:8765/projects`. It lists every project the hub has indexed, with its customer, SO/QTE, stage, open task count, last email date and folder path.
- **Create project manually** (top of the page) makes a project without waiting for an email. Fill in Customer (required), SO numbers, QTE numbers, Project # (7 digits, optional), Description, Level and Folder name; separate several SO or QTE numbers with commas or spaces. Leave Folder name blank and the hub names the folder from the customer, the first SO (or QTE) and the project number. It creates the folder, the note and the time-estimate sheet from the template, then opens the project page. Add links (SharePoint, email, etc.) on the project page afterwards.
- **Open folder** opens the project folder in Explorer.
- **Archive** moves the folder into `_Archive` inside the vault. Archived projects drop out of the board, the timeline and the project list. The **Archived** section lists them with **Restore** (moves the folder back) and **Open folder**.
- **Delete** asks a simple OK/Cancel confirm. The folder is then moved to `backups\deleted_projects\<YYYYmmdd_HHMMSS>_<folder name>`, never erased. Its saved links are removed and any pending assignment for it is marked deleted. The **Deleted** section lists these backups newest first, with **Restore** (moves the folder back under its original name) and **Open folder**.
- If a file in the folder is open (Excel, Word, a PDF viewer), Windows can refuse the move. Close it and try again; the hub says "Could not move the folder (is a file open?)".
- Restore never overwrites: if a folder with that name is already in the vault, you get a message instead.
- Buttons that start work (**Refresh now**, Create, Link, Simulate, Archive, Delete, Restore, Open as tab group) show a small spinner and are disabled until the work finishes.

### 2.9 Test the new-assignment workflow
You don't need an email to try it:
1. Open **Projects** and click **Simulate new assignment** under "Test the new-assignment workflow". The hub adds a fake assignment (customer `TEST Customer`, a `QTE-9xxxxx` number and a `0299xxx` project number that don't collide with anything you have) and takes you to the Dashboard.
2. The banner shows it with a **TEST** badge. **Create project**, **Link to existing folder** and **Dismiss** work exactly as they do for a real assignment.
3. **Create project** makes a real folder named `TEST Customer QTE-9xxxxx 0299xxx` in the vault, then opens its project page once the refresh finishes.
4. Delete it from **Projects** when you're done (see 2.8).

### 2.10 Team tracker sheet (read-only)
The hub reads the team's **APE Project Tracking** workbook and shows it next to your own data. It is read-only in both directions: nothing is written back to the sheet, and the sheet never changes your notes.

What you see:
- A **Tracker sheet** panel on the project page with the PM, ME, support and AE names (full names with initials), sales manager, project ID, instrument, level, APE lab build/test, SO status, health, assigned date, baseline delivery, ship date, instr need-by and the comments column.
- A **sheet: RISK** chip on a board card when the sheet's Project Health is RISK or ISSUE, and a **sheet: 06 - Fabrication** chip when the sheet's SO status sits in a different part of the workflow than the hub's stage.
- **FROM TRACKER SHEET** entries in the Dashboard banner when the sheet lists a project assigned to you that the hub does not have yet. Create, Link and Dismiss work exactly as they do for an email assignment.
- A ship date taken from the sheet when no email supplied one. The project page marks it "(from sheet)".
- A line at the top of the Dashboard with the sheet's file date, which copy it read (OneDrive-synced or Downloads) and an **Open sheet** link to the SharePoint copy.

Safety: the tracker copies the workbook into `data\ape_tracker` and reads only that copy. The original is never opened for writing, never opened in Excel and never saved. Only the Active Projects, New Projects and Dropdowns tabs are read; the $ amount columns are ignored.

Keeping it fresh automatically:
1. In SharePoint open **General › Tracking Documents** and click **Sync** (or **Add shortcut to My files** on the folder). "Add shortcut to My files" on the single file only makes a `.url` web link, which is not the workbook.
2. Delete any old `APE Project Tracking*.url` shortcuts from your OneDrive folder.
3. The hub finds `Tracking Documents\APE Project Tracking.xlsx` under your OneDrive sync folders by itself, so no path is needed in Settings.
4. Don't open and save the synced copy in desktop Excel; let OneDrive keep it as the server copy.

Until the folder is synced, the hub uses the newest `APE Project Tracking*.xlsx` in your Downloads folder (File > Download a copy). After 48 hours without a fresh download the Dashboard line turns amber and says the downloaded copy is old.

### 2.11 Timesheet
Open **Timesheet** in the sidebar, or `http://localhost:8765/timesheet`. It shows one row per project for a week (Monday to Sunday by default) with **Prev**, **This week**, **Next** and a start/end date form for any other range up to 31 days.

How hours get there:
- A task you ticked and logged time on is split equally over the days you recorded for it, and the part that falls inside the period lands in that day's cell. Hover a cell to see which task or meeting it came from.
- Outlook meetings that mention a project's SO, QTE or project number add their length in hours on the meeting day.
- A dot marks a day you worked on a project without logging hours: a task you ticked or an email about it. Those rows appear with zero hours so you remember them.
- Project IDs come from the tracker sheet, matched by SO, QTE or project number. A row whose project is not in the sheet says "not found in sheet" and shows the reference it tried.

Editing:
- Type hours in any cell. The cell gets an outline and the row and day totals update. Clearing the box removes your edit and the calculated value comes back.
- **Spread** takes a weekly total for a row and shares it over the days that already have hours or activity (weekdays if there are none), so the days add up to exactly that total.
- **Copy** next to a project ID copies the ID; **Copy week as text** copies the whole grid as tab-separated lines for pasting into a timesheet.
- Day totals turn amber under the target (8 h by default) and red over it, on weekdays only.

Edits are stored in the hub's own `state.json` and never change your notes or the tracker sheet.

---

## Part 3: Monday update (7 AM, before the 8 AM meeting)
1. Click **Refresh now** on the dashboard, or rely on the 06:30 run.
2. Open `http://localhost:8765/monday`.
3. For each project it shows:
   - stage and ship date;
   - **since last Monday**: new project emails, and boxes you ticked in the hub with the date you ticked them;
   - **due this week**;
   - **overdue**.
4. Click **Copy** and paste the text into your meeting notes or a Teams message. Edit before sending.

Ticks made outside the hub (typed directly in the `.md`) show up in the note but don't have a tick date on the Monday page.

---

## Part 4: Customising

- **Workflow steps and offsets**: `tracker\rules.yaml`. `triggers` holds the subject patterns for each email type. `workflow` holds each step, its stage and its offset in business days from a trigger. After editing, run `python -m tracker.tracker --dry-run` to check.
- **Note template**: `templates\Project Template.md`, used when **Create project** makes a new note.
- **PM schedule rows to keep**: `pm_schedule_include` in `tracker\config.yaml`, a regular expression on the row name.
- **How far back emails are read**: `outlook_com.days_back` in `config.yaml`, or `--since N` on the command line.
- **Holidays**: `holidays` in `config.yaml`. Add next year's dates each December.

---

## Part 5: Troubleshooting

| Symptom | What to check |
|---|---|
| `http://localhost:8765` doesn't load | The hub isn't running. Run `Start-ScheduledTask -TaskName ProjectHub`, or double-click `run_hub.cmd` to see errors. Hidden-mode errors go to `output\hub.log`. |
| No new emails appear | Classic Outlook must be installed and signed in; "New Outlook" can't be read. See `output\hub_last_run.log` and `output\last_run.log`. |
| Refresh seems stuck | Outlook may be showing a dialog. Bring it to the front. The tracker gives up after 10 minutes. |
| A project isn't picked up | The folder must be a direct child of the vault and its name or note must contain the SO-/QTE- number or the 7-digit project number. `General` and folders starting with `.` are skipped. |
| An email went to the wrong step | Use the triage buttons, or set `email_overrides` in `tracker\state.json`. |
| A note didn't update | Very long paths are handled automatically. Check that the file isn't open with unsaved changes elsewhere, and look in `output\last_run.log`. |
| "Open as tab group" does nothing | The extension isn't loaded (see 1.7), or it was reloaded; click **Reload** on **Project Hub Tab Groups** in `chrome://extensions`, then refresh the hub page. The button always ends with a toast saying why it failed. |
| A recurring task didn't add the next copy | The task must have a `🔁` rule and be ticked `[x]` or `[-]`. If an open copy with the same title, rule and due date already exists, no second copy is added. |
| Save shows a conflict warning | The file changed elsewhere. Reload to see the latest version, or overwrite on purpose. |
| Project page said Not Found right after Create | Fixed: the page now waits for the refresh and shows "Setting up project" until the project is indexed. If it stays there, check `output\hub_last_run.log`. |

Logs: `output\last_run.log` (tracker runs), `output\hub_last_run.log` (latest hub refresh), `output\scheduler.log` (06:30 task), `output\hub.log` (hidden hub). A static fallback dashboard is written to `output\dashboard.html` on every run.

---

## Part 6: Backups and restore
- Before the one-time note migrations, copies of every note were saved under `backups\<date-time>-...\`.
- OneDrive version history is also available for every note (right-click > Version history).
- `tracker\state.json` stores pending assignments, triage choices, due overrides, tick dates and your added links. Copy it if you want a backup.

---

## Part 7: Command reference
Run from `<ProjectHub folder>` (or double-click `run_tracker.cmd` / `run_hub.cmd`, which use `.venv` when it exists):
```bash
.venv\Scripts\python.exe -m tracker.tracker --dry-run      # preview, writes nothing
.venv\Scripts\python.exe -m tracker.tracker                # normal run
.venv\Scripts\python.exe -m tracker.tracker --since 30     # only emails from the last 30 days
.venv\Scripts\python.exe -m tracker.hub                    # run the hub in this window
.venv\Scripts\python.exe -m unittest discover -s tests     # self-tests
```

---

## Part 8: Removing everything
```bash
powershell.exe -NoProfile -Command "Unregister-ScheduledTask -TaskName ProjectHub -Confirm:$false"
powershell.exe -NoProfile -Command "Unregister-ScheduledTask -TaskName ProjectTracker -Confirm:$false"
```
Then remove the extension at `chrome://extensions`, delete the `Project Hub.url` shortcut from your desktop, and delete the `<ProjectHub folder>` (including `.venv`) if you no longer want it. Your project notes and folders stay in OneDrive. The tracker sections in the notes are plain Markdown and can stay or be deleted.

---

## Obsidian (optional, not needed)
The hub replaces Obsidian for this workflow. If you ever want Obsidian anyway, see `templates\OBSIDIAN_SETUP.md`. The notes are compatible with the Obsidian Tasks plugin format.
