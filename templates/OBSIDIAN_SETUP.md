# Obsidian setup (optional)

Obsidian is no longer needed: the Project Hub (`http://localhost:8765`) replaces it. See `USER_GUIDE.md` in the repo root for setup and daily use. The steps below are only for using Obsidian alongside the hub.

1. Install Obsidian from https://obsidian.md and launch it.
2. Choose "Open folder as vault" and select your project folder root (the `vault` value in `tracker/config.yaml`, for example
   `%OneDrive%\Desktop\Projects\Project Specific Documents`).
3. Settings > Community plugins > Turn on community plugins > Browse, then install and enable:
   - Tasks (required for the checkbox queries)
   - Dataview (required for the project tables)
   - Optional: Full Calendar, Kanban
4. Copy `templates/Dashboard.md` into the vault root. The tracker does this automatically on a normal run when `install_dashboard_note: true` in `tracker/config.yaml` and the file is not already there.
5. Emails: the tracker reads **classic Outlook** directly (Inbox and Calendar for the last 120 days; your sent mail is not used; set in `tracker/config.yaml` under `outlook_com`). Classic Outlook should be set up. If it isn't open at run time, the tracker starts it in the background. No manual steps are needed. Optional extras: `.eml`/`.msg` files in `email_drop/` or in project folders are also read. `templates/POWER_AUTOMATE_SETUP.md` is a fallback if classic Outlook is ever retired.
6. Read-only checkboxes: the tracker only **adds** unticked tasks with due dates. It never ticks, strikes or closes anything, and it doesn't treat struck-through TO DO text or your sent emails as "done". You decide each one: `- [x]` done, or `- [-]` not needed for this project (Obsidian Tasks calls this "cancelled"). Marked lines are kept exactly as you wrote them; the dashboard shows them as Done or Not needed and leaves them out of the overdue count.
6. Run commands from `<ProjectHub folder>`:
   - `.venv\Scripts\python.exe -m tracker.tracker --dry-run` preview only, writes nothing
   - `.venv\Scripts\python.exe -m tracker.tracker` normal run
   - `.venv\Scripts\python.exe -m tracker.tracker --since 30` limit how far back emails are read
   - `.venv\Scripts\python.exe -m tracker.migrate_notes --dry-run` preview the one-time note migration
7. Scheduled task: `install_schedule.ps1` registers "ProjectTracker" for the current user, weekdays at 06:30, running `run_tracker.cmd`. It is not run automatically. Remove it with:
   `Unregister-ScheduledTask -TaskName "ProjectTracker" -Confirm:$false`
8. Editing deadlines: change offsets, subject regexes and done patterns in `tracker/rules.yaml`. Offsets are business days; holidays come from `holidays` in `tracker/config.yaml`.
