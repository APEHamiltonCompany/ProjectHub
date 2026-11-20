from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tracker import notes
from tracker import rules as rules_mod
from tracker.hub import tasks as hub_tasks
from tracker.models import Email, Project, Task
from tracker.tracker import apply_due_overrides, merge_block_state, build_snapshot, build_tasks, cascade_done, furthest_stage, resolve_level

RULES = rules_mod.load_rules(ROOT / "tracker" / "rules.yaml")
TODAY = date(2026, 11, 20)
SCHEDULE = [
    {"name": "Acme - SO-111111 PreFabApps", "start": "2026-10-01", "finish": "2026-10-20"},
    {"name": "Send Test Plan for Review & List of Parts for Testing", "start": "2026-10-21", "finish": "2026-10-28"},
    {"name": "Testing", "start": "2027-01-04", "finish": "2027-01-29"},
    {"name": "AE Pack Up", "start": "2027-02-01", "finish": "2027-02-03"},
    {"name": "AutoE Close Out", "start": "2027-02-04", "finish": "2027-02-12"},
    {"name": "Ship Date", "start": "2027-02-15", "finish": "2027-02-15"},
    {"name": "Approvals", "start": "2026-10-10", "finish": "2026-10-12"},
]


def project() -> Project:
    return Project(folder="x", name="SO-111111", project_no="0207001", so=["SO-111111"])


def by_id(tasks: list[Task]) -> dict[str, Task]:
    return {task.id.split("-", 1)[1]: task for task in tasks}


class MilestoneMatchTests(unittest.TestCase):
    def test_row_names(self):
        cases = {
            "PreFabApps": "ms_prefab",
            "Send Test Plan for Review & List of Parts for Testing": "ms_test_plan",
            "Testing": "ms_testing",
            "Pre-FAT": "ms_pre_fat",
            "Field Apps & iFAT": "ms_ifat",
            "FAT": "ms_fat",
            "AE Pack Up": "ms_pack_up",
            "Pack Up": "ms_pack_up",
            "AE Disassembly & Packing": "ms_pack_up",
            "AutoE Close Out": "ms_closeout",
        }
        for name, expected in cases.items():
            self.assertEqual((rules_mod.milestone_for(RULES, name) or {}).get("id"), expected, name)
        for name in ("FAT Approvals", "ME Disassembly & Packing", "HWE Close Out", "Ship Date", "Approvals", "KO Call"):
            self.assertIsNone(rules_mod.milestone_for(RULES, name), name)


class LevelTests(unittest.TestCase):
    def test_parse_level(self):
        self.assertEqual(rules_mod.parse_level("L2R"), ("L2", True))
        self.assertEqual(rules_mod.parse_level("3"), ("L3", False))
        self.assertEqual(rules_mod.parse_level("tbd"), ("", False))

    def test_resolve_level_order(self):
        self.assertEqual(resolve_level("", "l1r", "L3"), "L1R")
        self.assertEqual(resolve_level("L4", "L1", ""), "L4")
        self.assertEqual(resolve_level("", "", ""), "")

    def test_level_filter(self):
        tasks = by_id(build_tasks(project(), {"kickoff": date(2026, 9, 10)}, RULES, [], TODAY, level="L1")[0])
        self.assertIn("test_matrix", tasks)
        self.assertNotIn("wiring_diagram", tasks)
        self.assertNotIn("app_dev", tasks)
        self.assertNotIn("ms_fat", tasks)

    def test_repeat_hides_design_steps(self):
        tasks = by_id(build_tasks(project(), {"kickoff": date(2026, 9, 10)}, RULES, [], TODAY, level="L2R")[0])
        for step in ("wiring_diagram", "test_matrix", "design_review", "apps_plan"):
            self.assertNotIn(step, tasks)
        self.assertIn("kickoff_meeting", tasks)

    def test_fat_without_chart_only_for_l3_l4(self):
        l4 = by_id(build_tasks(project(), {"kickoff": date(2026, 9, 10)}, RULES, [], TODAY, level="L4")[0])
        self.assertIn("ms_fat", l4)
        self.assertIn("fat", l4)
        l2 = by_id(build_tasks(project(), {"kickoff": date(2026, 9, 10)}, RULES, [], TODAY, level="L2")[0])
        self.assertNotIn("ms_fat", l2)
        self.assertNotIn("fat", l2)

    def test_chart_without_fat_rows_drops_fat_even_on_l4(self):
        tasks = by_id(build_tasks(project(), {"kickoff": date(2026, 9, 10)}, RULES, [], TODAY, SCHEDULE, level="L4")[0])
        self.assertNotIn("ms_fat", tasks)
        self.assertNotIn("ifat", tasks)


class BuildTasksTests(unittest.TestCase):
    def setUp(self):
        self.tasks, self.stage = build_tasks(
            project(),
            {"assignment": date(2026, 9, 1), "kickoff": date(2026, 9, 10), "design_review": date(2026, 10, 5)},
            RULES,
            [],
            TODAY,
            SCHEDULE,
            level="L2",
        )
        self.by_id = by_id(self.tasks)

    def test_milestone_dates_from_chart(self):
        self.assertEqual(self.by_id["ms_prefab"].due, date(2026, 10, 20))
        self.assertTrue(self.by_id["ms_prefab"].milestone)
        self.assertEqual(self.by_id["ms_testing"].due, date(2027, 1, 29))

    def test_steps_inherit_milestone_date(self):
        self.assertEqual(self.by_id["adp_start"].due, date(2026, 10, 20))
        self.assertEqual(self.by_id["adp_start"].parent, self.by_id["ms_prefab"].id)
        self.assertEqual(self.by_id["app_dev"].due, date(2027, 1, 29))
        self.assertEqual(self.by_id["pack_up"].due, date(2027, 2, 3))

    def test_chart_finish_date_wins_over_trigger_email(self):
        self.assertEqual(self.by_id["design_review"].due, date(2026, 10, 20))
        self.assertEqual(self.by_id["kickoff_meeting"].due, date(2026, 10, 20))
        self.assertEqual(self.by_id["apps_plan"].due, date(2026, 10, 28))

    def test_trigger_email_dates_step_when_no_chart(self):
        tasks = by_id(build_tasks(project(), {"kickoff": date(2026, 9, 10), "design_review": date(2026, 10, 5)}, RULES, [], TODAY, level="L2")[0])
        self.assertIsNone(tasks["ms_prefab"].due)
        self.assertEqual(tasks["design_review"].due, date(2026, 10, 5))
        self.assertEqual(tasks["apps_plan"].due, date(2026, 10, 12))
        self.assertIsNone(tasks["adp_start"].due)

    def test_pre_kickoff_steps_from_assignment(self):
        self.assertEqual(self.by_id["review_cpr_so"].due, date(2026, 9, 3))
        self.assertEqual(self.by_id["review_cpr_so"].parent, "")

    def test_order_parent_then_children(self):
        ids = [task.id.split("-", 1)[1] for task in self.tasks]
        self.assertLess(ids.index("time_estimate"), ids.index("ms_prefab"))
        self.assertEqual(ids[ids.index("ms_prefab") + 1], "adp_start")
        self.assertLess(ids.index("ms_test_plan"), ids.index("apps_plan"))

    def test_no_pm_schedule_rows(self):
        self.assertEqual([task.title for task in self.tasks if task.stage == notes.PM_HEADING], [])
        self.assertNotIn("Ship Date", [task.title for task in self.tasks])

    def test_every_dated_child_uses_its_milestone_finish(self):
        for task in self.tasks:
            if task.parent:
                parent = next(item for item in self.tasks if item.id == task.parent)
                self.assertEqual(task.due, parent.due, task.id)


class FurthestStageTests(unittest.TestCase):
    def test_furthest_bucket_wins_over_latest_date(self):
        triggers = {"at_notification": date(2026, 11, 1), "design_review": date(2026, 11, 15)}
        self.assertEqual(furthest_stage(triggers, RULES), "System Test")

    def test_unknown_when_no_triggers(self):
        self.assertEqual(furthest_stage({}, RULES), "Unknown")


class CascadeDoneTests(unittest.TestCase):
    def _tasks(self):
        return [
            Task(id="p-ms_prefab", title="PreFabApps", milestone=True),
            Task(id="p-a", title="A", parent="p-ms_prefab"),
            Task(id="p-b", title="B", parent="p-ms_prefab"),
        ]

    def test_ticked_parent_ticks_children(self):
        tasks = self._tasks()
        tasks[0].done, tasks[0].mark = True, "x"
        cascade_done(tasks, TODAY)
        self.assertTrue(all(task.done and task.mark == "x" for task in tasks))

    def test_all_children_done_ticks_parent(self):
        tasks = self._tasks()
        tasks[1].done, tasks[1].mark = True, "x"
        tasks[2].done, tasks[2].mark = True, "-"
        cascade_done(tasks, TODAY)
        self.assertTrue(tasks[0].done)
        self.assertEqual(tasks[0].mark, "x")

    def test_partial_children_leave_parent_open(self):
        tasks = self._tasks()
        tasks[1].done, tasks[1].mark = True, "x"
        cascade_done(tasks, TODAY)
        self.assertFalse(tasks[0].done)


class NoteRenderTests(unittest.TestCase):
    def test_children_indented_and_cascade_written(self):
        tasks = [
            Task(id="p-review", title="Review CPR", stage="Pre-Kickoff"),
            Task(id="p-ms_prefab", title="PreFabApps", stage="Pre-Fabrication", milestone=True, done=True, mark="x"),
            Task(id="p-a", title="Step A", stage="Pre-Fabrication", parent="p-ms_prefab", done=True, mark="x"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "n.md"
            path.write_text("**TO DO:**\n", encoding="utf-8")
            notes.apply_update(path, {}, tasks, TODAY)
            text = path.read_text(encoding="utf-8")
            self.assertLess(text.index("**Pre-Kickoff**"), text.index("**Pre-Fabrication**"))
            self.assertIn("\n- [x] PreFabApps", text)
            self.assertIn("\n    - [x] Step A", text)
            changed, _diff = notes.apply_update(path, {}, tasks, TODAY)
            self.assertFalse(changed)


class HubCascadeTests(unittest.TestCase):
    TEXT = (
        "<!-- tracker:start (auto-managed; tick boxes freely) -->\n"
        "**Pre-Fabrication**\n"
        "- [ ] PreFabApps \U0001F194 p0207001-ms_prefab\n"
        "    - [ ] Step A \U0001F194 p0207001-adp_start\n"
        "    - [ ] Step B \U0001F194 p0207001-wiring_diagram\n"
        "- [ ] Send Test Plan \U0001F194 p0207001-ms_test_plan\n"
        "<!-- tracker:end -->"
    )

    def _tick(self, text, task_id, mark):
        new_text, row = hub_tasks.set_mark(text, task_id=task_id, mark=mark)
        return hub_tasks.cascade_marks(new_text, row["index"])

    def test_parent_tick_ticks_children(self):
        text, changed = self._tick(self.TEXT, "p0207001-ms_prefab", "x")
        self.assertIn("    - [x] Step A", text)
        self.assertIn("    - [x] Step B", text)
        self.assertIn("- [ ] Send Test Plan", text)
        self.assertEqual({item["id"] for item in changed}, {"p0207001-adp_start", "p0207001-wiring_diagram"})

    def test_last_child_ticks_parent_and_reopen_unticks(self):
        text, _ = self._tick(self.TEXT, "p0207001-adp_start", "x")
        self.assertIn("- [ ] PreFabApps", text)
        text, changed = self._tick(text, "p0207001-wiring_diagram", "-")
        self.assertIn("- [x] PreFabApps", text)
        self.assertEqual(changed, [{"id": "p0207001-ms_prefab", "mark": "x"}])
        text, changed = self._tick(text, "p0207001-adp_start", " ")
        self.assertIn("- [ ] PreFabApps", text)

    def test_parent_untick_reopens_children(self):
        text, _ = self._tick(self.TEXT, "p0207001-ms_prefab", "x")
        text, _ = self._tick(text, "p0207001-ms_prefab", " ")
        self.assertIn("    - [ ] Step A", text)
        self.assertIn("    - [ ] Step B", text)


class StaleOverrideTests(unittest.TestCase):
    def test_blank_and_redundant_overrides_dropped(self):
        tasks = [Task(id="a", title="A", due=date(2026, 10, 20)), Task(id="b", title="B", due=date(2026, 10, 20)),
                 Task(id="c", title="C", due=date(2026, 10, 20)), Task(id="d", title="D", due=date(2026, 10, 20))]
        overrides = {"a": {"due": None, "note": ""}, "b": {"due": "2026-10-20", "note": ""},
                     "c": {"due": "2026-10-25", "note": ""}, "d": {"due": None, "note": "blocked"}}
        apply_due_overrides(tasks, {}, overrides, {})
        self.assertEqual(sorted(overrides), ["c", "d"])
        self.assertEqual(tasks[0].due, date(2026, 10, 20))
        self.assertEqual(tasks[2].due, date(2026, 10, 25))

    def test_override_equal_to_old_computed_date_dropped(self):
        tasks = [Task(id="a", title="A", due=date(2026, 9, 11))]
        overrides = {"a": {"due": "2026-09-14", "note": ""}}
        apply_due_overrides(tasks, {"a": {"mark": " ", "due": date(2026, 9, 14)}}, overrides, {"a": "2026-09-14"})
        self.assertEqual(overrides, {})
        self.assertEqual(tasks[0].due, date(2026, 9, 11))

    def test_note_line_without_date_is_not_an_override(self):
        tasks = [Task(id="a", title="A", due=date(2026, 10, 20))]
        overrides = {}
        apply_due_overrides(tasks, {"a": {"mark": " ", "due": None}}, overrides, {"a": "2026-09-01"})
        self.assertEqual(overrides, {})
        self.assertEqual(tasks[0].due, date(2026, 10, 20))


class OldPmRowTests(unittest.TestCase):
    BODY = (
        "<!-- tracker:start (auto-managed; tick boxes freely) -->\n"
        "**PM schedule**\n"
        "- [x] Medimmune - SO-290635 PreFabApps \U0001F4C5 2026-10-23 \u2705 2026-10-22 \U0001F194 p0207001-pm-medimmune_prefabapps-3880a7\n"
        "- [-] HWE Close Out \U0001F4C5 2027-02-01 \U0001F194 p0207001-pm-hwe_close_out-695377\n"
        "- [ ] Ship Date \U0001F4C5 2027-02-15 \U0001F194 p0207001-pm-ship_date-111111\n"
        "<!-- tracker:end -->\n"
    )

    def test_ticked_pm_row_ticks_milestone_and_old_rows_dropped(self):
        tasks, _stage = build_tasks(project(), {"kickoff": date(2026, 9, 10)}, RULES, [], TODAY, SCHEDULE, level="L2")
        merge_block_state(tasks, self.BODY, RULES)
        prefab = next(task for task in tasks if task.id.endswith("ms_prefab"))
        self.assertTrue(prefab.done)
        self.assertEqual(prefab.mark, "x")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "n.md"
            path.write_text("**TO DO:**\n\n" + self.BODY, encoding="utf-8")
            notes.apply_update(path, {}, tasks, TODAY)
            text = path.read_text(encoding="utf-8")
        self.assertNotIn("-pm-", text)
        self.assertNotIn("**PM schedule**", text)
        self.assertIn("- [x] PreFabApps", text)


class SnapshotInfoTests(unittest.TestCase):
    def test_kickoff_details_survive_email_rows(self):
        proj = project()
        email = Email("m1", "Acme | SO-111111 | Kickoff", "pm@x", "me", "", datetime(2026, 9, 10, tzinfo=timezone.utc), "")
        info = {proj.key: {"cpr": "https://cpr", "links": [{"title": "CPR", "url": "https://cpr"}], "team": ["A"]}}
        snap = build_snapshot(TODAY, [proj], {}, {proj.key: [(email, "kickoff", False)]}, info, {}, [], "gen")
        row = snap["projects"][0]
        self.assertEqual(row["cpr"], "https://cpr")
        self.assertEqual(row["team"], ["A"])
        self.assertEqual(len(row["links"]), 1)
        self.assertEqual(len(row["emails"]), 1)


if __name__ == "__main__":
    unittest.main()
