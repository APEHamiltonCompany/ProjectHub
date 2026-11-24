# ProjectHub handoff (2026-11-24)

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

## Upcoming features (post-rollout backlog, to implement gradually)
- [ ] **Drafting for Apps Support emails**
  - Pull additional links from project emails, see project-support-email.md
- [ ] **Search repeat projects for compliance pages on the R: drive**
  - Upload project compliance pages to the R: drive on project completion
- [ ] **Explore cases for repeat projects with multiple different 020- parts**
- [ ] **Create Parts List entries in SharePoint**
- [ ] **Style with Hamilton colors**


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
