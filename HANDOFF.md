# ProjectHub handoff (2026-11-20)

Start a new chat with: "Read HANDOFF.md and continue." It covers the current state, the uncommitted work, how to run things, and the open items.

## Who and how
- User: Cristian Uribe, Applications Engineer (APE team, 5 people). Windows, repo `C:\repos\ProjectHub`, branch `main`.
- Working style:
  - Propose a plan and wait for "go ahead" before large changes.
  - Answer questions without editing.
  - Don't commit or push unless asked. The agent can commit; the user pushes (`git push origin main`), because pushing is blocked for the agent.
  - Back up `tracker/config.yaml` before editing it. It's gitignored and is the user's own config.
- Standing rules:
  - No Fabrication checklist steps, ever. Fabrication is a bucket only.
  - Only create a project from an assignment email when the user is on the **To:** line (not CC).
  - On-Going projects are excluded from the tracker-sheet sync.

## Environment
- Python 3.12 venv at `.venv`.
- Tests: `.venv/Scripts/python -m unittest discover -s tests`. Last result: **471 tests OK, 1 skipped**.
- Dry run: `.venv/Scripts/python -m tracker.tracker --dry-run`. Real run: drop `--dry-run`. The run log is `output/last_run.log`; the hub's runs log to `output/hub_last_run.log`.
- Hub: http://localhost:8765, started by scheduled task `ProjectHub`.
  - Restart (PowerShell): `Stop-ScheduledTask -TaskName ProjectHub`, kill python processes whose command line has `tracker.hub`, then `Start-ScheduledTask -TaskName ProjectHub`.
  - A restart triggers a full tracker run, about 11 minutes.
- Data:
  - `output/snapshot.json`: what the hub shows.
  - `output/outlook_cache.json`: last 120 days of mail.
  - `tracker/state.json`: due overrides, email overrides, pending items, ticks.
  - Vault: `R:/APE_AE/Cristian_Uribe/PROJECTS/My Projects/<Customer>/SO-######`.
- Outlook: classic Outlook through COM. COM calls sometimes fail with "Server execution failed"; retry later. The Online Archive is a separate store and isn't in the cache.
- Use `$TMPDIR` for scratch files, not `/tmp`.
- To wait on the tracker, don't poll for `*tracker.tracker*` in process command lines: the waiting shell matches itself.

## Committed so far
- `93c9b1c` KO timeline milestones with nested steps, level filter, finish-date scheduling. **Not pushed yet.**
- Earlier work: `f94dcda` backups/offline mode, `33d96b5` six buckets + file templates + sorting, `a0f7ef1` merge with upstream.

## UNCOMMITTED work (ready to commit; tests pass)
Files:
- `tracker/tracker.py`, `extract.py`, `rules.py`, `rules.yaml`, `models.py`
- `hub/app.py`, `hub/templates/project.html`
- `USER_GUIDE.md`
- new `tests/test_timelines.py`

What it does:
1. **Newest PM table wins, per milestone.**
   - Dates come from the newest table from a trusted PM in any project email: the KO email, an A&T notification, a "KO"+ID subject, or a PM reply. For a reply, only the text above the quoted thread counts (`extract.own_text`).
   - A trimmed table only updates the milestones it lists.
   - Code: `collect_timelines` → `merge_timelines` in `tracker/tracker.py`.
2. **Trusted senders:** the project's KO senders, plus the PM on the tracker sheet. A teammate quoting an old table doesn't count.
3. **KO subject rule:** a whole-word "KO" counts as Kickoff only if the subject also has an SO/QTE/020 number (`rules.py`).
4. **Summary row:** only the real project summary row is dropped (the row whose dates span all the other rows). Trimmed tables keep their first row.
5. **Wrong-table check:**
   - If none of the SO/QTE/020 numbers in a table's rows matches the subject or the project's numbers, the whole table is skipped and no dates change.
   - The run log gets a line: `timeline skipped: <date> <subject> (subject/project ..., table ...)`. Log only, as the user asked; no notice in the hub.
   - One matching number is enough. Customer names are never compared ("AstraZeneca" subject with "Medimmune" rows is fine).
6. **Project choice:** both the dates and the stage use the first project number in the subject (`subject_project`). Example: "Amazon SO-283465 … (Previously SO-268726)" no longer moves the Corteva SO-268726 project.
7. **Date source shown:** the project page shows where each date came from, e.g. "2026-12-11 (A&T 11/16)".

Verified on the hub's real 12:30 run: no active project's milestone dates changed. Neogenomics (0205423, Completion) gained 5 dates from Scott's 9/22 table, because the subject's SO now counts as a match.

## Scheduling model (built; see USER_GUIDE 2.6/2.6a, `tracker/rules.yaml`)
- **Buckets:** Pre-Kickoff, Pre-Fabrication, Fabrication, System Test, Closeout, Completion.
- **Stage:** the furthest bucket any trigger email reached. A manual stage overrides it.
- **Milestones** (parent tasks; only these KO rows count):
  - PreFabApps
  - Send Test Plan for Review & List of Parts for Testing
  - Testing
  - Pre-FAT, Field Apps & iFAT, FAT (optional)
  - AE Pack Up (also matches "Pack Up" and "AE Disassembly")
  - AutoE Close Out
  - All other PM rows are ignored. The PM schedule section was removed.
- **Steps** are nested under their milestone and take its **Finish** date.
  - A trigger offset only applies when the milestone has no date.
  - Pre-Kickoff steps are assignment + 2 business days.
- **Two-way checking:** ticking a milestone ticks all its steps, and the last step ticked ticks the milestone. This works in the tracker run (`cascade_done`) and when ticking in the hub (`hub/tasks.py cascade_marks`).
- **Level** comes from the note, then the tracker sheet, then the KO email.
  - L1: no wiring diagram, app dev, system test or methods steps.
  - Repeat projects ("R" suffix): no design steps.
  - FAT steps appear only if the timeline table includes them, or on L3/L4 when there is no readable table.
- **Undated later-bucket steps stay visible** in Focus "No date". The user said to leave them for now.
- Approved milestone → steps grouping: PreFabApps (ADP start, kickoff, wiring diagram, test matrix, design review); Send Test Plan (Apps Plan, parts for testing); Testing (app dev, system setup, device testing, troubleshooting); Pre-FAT/iFAT/FAT (iFAT, FAT, post-FAT); AE Pack Up (pack up); AutoE Close Out (closeout docs, closeout reply).

## PM table format (for the user's request to the PMs)
- **Columns:** Task Name, Duration, Start, Finish.
  - One extra non-date column (e.g. Predecessors) between Duration and Start is fine.
  - Resource Names after Finish is fine.
- **Duration** is only used to find where each row starts. It must look like `N day(s)` (decimals and `?` are OK).
- **Dates:** M/D/YY or M/D/YYYY, with an optional weekday.
- **Images can't be read.** 8 of 25 KO emails have the timeline as a pasted image: Aman Aman (5), Cindy Heinrich (2), Matt Sturdivant (1).

## Open / next items
1. **Commit the uncommitted work** when the user says to; the user then pushes. Offer this.
2. Check in a browser that the project page shows nested milestones with date sources. So far only the HTML has been checked.
3. One due override left in `tracker/state.json`: CUNY SO-288406 `wiring_diagram` = 2026-09-29. The user may have set it by hand; ask before removing it.
4. Later, after the structure settles: trim redundant tasks and redesign the Focus dashboard and project views.
5. Leftover junk: the `pre-merge backup` git stash (user to drop) and the untracked `ProjectHubView.png`.

## Pinned or parked (don't start until the user reopens it)
- **Project Support Email draft button + CPR/DR link extraction.** Full plan in agent memory `project-support-email.md`. The CPR/links snapshot bug that plan mentions is fixed: 19 projects now have a CPR and 32 have links.
- **Team rollout** (shared team views, reassigning projects): parked. Fix the user's own hub first, then test on a second machine.

## Key code locations
- `tracker/tracker.py`:
  - triggers: `collect_trigger_dates`
  - timeline tables: `subject_project`, `table_mismatch`, `foreign_row`, `collect_timelines`, `trusted_senders`, `merge_timelines`
  - tasks and checking: `build_tasks`, `cascade_done`
  - stage, level, overrides, snapshot: `furthest_stage`, `resolve_level`, `apply_due_overrides`, `build_snapshot`, `run`
- `tracker/extract.py`: `parse_schedule`, `own_text`, `strip_project_prefix`, `parse_kickoff_body`.
- `tracker/rules.py` + `rules.yaml`: `classify`, `milestone_for`, `parse_level`, `milestones:`, `workflow:`.
- `tracker/matcher.py`: SO/QTE/020 regexes. Underscores count as separators.
- `tracker/notes.py`: note checklist rendering (indented steps, Pre-Kickoff heading).
- `tracker/hub/app.py`: `live_tasks`, `checklist_order`, `group_by_stage(tree=True)`, `focus_buckets`.
- `tracker/hub/tasks.py`: `set_mark`, `cascade_marks`.
- Tests: `tests/test_milestones.py`, `tests/test_timelines.py`, `tests/test_core.py`, `tests/test_hub.py`.
