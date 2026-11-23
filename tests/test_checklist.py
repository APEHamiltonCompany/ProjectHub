from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tracker import notes
from tracker import rules as rules_mod
from tracker import settings as settings_mod
from tracker.hub.app import create_app
from tracker.models import Project, Task
from tracker.tracker import build_tasks

RULES = rules_mod.load_rules(ROOT / "tracker" / "rules.yaml")
TODAY = date(2026, 11, 20)
SCHEDULE = [
    {"name": "Acme - SO-111111 PreFabApps", "start": "2026-10-01", "finish": "2026-10-20"},
    {"name": "Send Test Plan for Review & List of Parts for Testing", "start": "2026-10-21", "finish": "2026-10-28"},
    {"name": "Testing", "start": "2027-01-04", "finish": "2027-01-29"},
    {"name": "AE Pack Up", "start": "2027-02-01", "finish": "2027-02-03"},
    {"name": "AutoE Close Out", "start": "2027-02-04", "finish": "2027-02-12"},
    {"name": "Ship Date", "start": "2027-02-15", "finish": "2027-02-15"},
]
REQUIRED = {
    "time_estimate",
    "apps_plan",
    "wiring_diagram",
    "test_matrix",
    "parts_for_testing",
    "system_setup",
    "device_cables",
    "testing_demo",
    "co_package_files",
    "co_labware",
    "co_parts_list",
    "co_adp_results",
    "co_device_controller",
    "co_installers",
    "co_wiki",
    "co_fat_templates",
}
HOST = {"Host": "127.0.0.1:8765"}
ORIGIN = "http://127.0.0.1:8765"
NOTE_TEXT = """---
project_no: "0207000"
customer: "Acme"
stage: "Pre-Fabrication"
---

**TO DO:**
"""


def project() -> Project:
    return Project(folder="x", name="SO-111111", project_no="0207001", so=["SO-111111"])


def by_id(tasks: list[Task]) -> dict[str, Task]:
    return {task.id.split("-", 1)[1]: task for task in tasks}


def step_ids(rules: dict) -> list[str]:
    return [str(step["id"]) for step in rules["workflow"]]


class ChecklistRulesTests(unittest.TestCase):
    def test_default_keeps_only_mandatory(self):
        rules = rules_mod.apply_checklist(RULES, {})
        self.assertEqual(sorted(step_ids(rules)), sorted(REQUIRED))
        self.assertEqual(len(REQUIRED), 16)
        for milestone in rules["milestones"]:
            self.assertFalse(milestone["enabled"], milestone["id"])
        self.assertEqual(len(rules["milestones"]), len(rules_mod.milestones(RULES)))

    def test_input_rules_not_mutated(self):
        before = [dict(step) for step in RULES["workflow"]]
        rules_mod.apply_checklist(RULES, {"checklist_optional": ["fat"]})
        self.assertEqual(RULES["workflow"], before)
        self.assertNotIn("enabled", RULES["milestones"][0])

    def test_mandatory_drops_levels_and_repeat(self):
        rules = rules_mod.apply_checklist(RULES, {})
        for step in rules["workflow"]:
            self.assertNotIn("levels", step, step["id"])
            self.assertNotIn("repeat", step, step["id"])

    def test_enabling_optional_step(self):
        rules = rules_mod.apply_checklist(RULES, {"checklist_optional": ["fat"]})
        self.assertIn("fat", step_ids(rules))
        self.assertNotIn("ifat", step_ids(rules))

    def test_enabling_milestone(self):
        rules = rules_mod.apply_checklist(RULES, {"checklist_optional": ["ms_testing"]})
        flags = {milestone["id"]: milestone["enabled"] for milestone in rules["milestones"]}
        self.assertTrue(flags["ms_testing"])
        self.assertFalse(flags["ms_fat"])

    def test_enabled_optional_ignores_bad_values(self):
        self.assertEqual(rules_mod.enabled_optional({}), set())
        self.assertEqual(rules_mod.enabled_optional({"checklist_optional": "fat"}), set())
        self.assertEqual(rules_mod.enabled_optional({"checklist_optional": ["fat", 3, None]}), {"fat"})

    def test_checklist_items_grouped_by_stage(self):
        rows = rules_mod.checklist_items(RULES)
        ids = [row["id"] for row in rows]
        expected = [m["id"] for m in RULES["milestones"]] + [s["id"] for s in RULES["workflow"]]
        self.assertEqual(sorted(ids), sorted(expected))
        self.assertEqual(len(ids), len(set(ids)))
        seen: list[str] = []
        for row in rows:
            if not seen or seen[-1] != row["stage"]:
                self.assertNotIn(row["stage"], seen)
                seen.append(row["stage"])
        for index, row in enumerate(rows):
            if row["kind"] != "milestone":
                continue
            kids = [s["id"] for s in RULES["workflow"] if s.get("milestone") == row["id"]]
            self.assertEqual(ids[index + 1:index + 1 + len(kids)], kids)

    def test_checklist_items_rows(self):
        rows = rules_mod.checklist_items(RULES)
        ids = [row["id"] for row in rows]
        self.assertEqual(sorted(ids), sorted([milestone["id"] for milestone in RULES["milestones"]] + step_ids(RULES)))
        milestones = [row for row in rows if row["kind"] == "milestone"]
        self.assertTrue(milestones)
        self.assertFalse(any(row["mandatory"] for row in milestones))
        self.assertEqual([row["id"] for row in milestones], [milestone["id"] for milestone in RULES["milestones"]])
        system_setup = next(row for row in rows if row["id"] == "system_setup")
        self.assertTrue(system_setup["mandatory"])
        self.assertEqual(system_setup["milestone"], "ms_testing")
        self.assertEqual(system_setup["stage"], "System Test")
        self.assertEqual(system_setup["kind"], "step")
        time_estimate = next(row for row in rows if row["id"] == "time_estimate")
        self.assertEqual(time_estimate["stage"], "Pre-Fabrication")
        self.assertEqual(time_estimate["milestone"], "")


class BuildTasksChecklistTests(unittest.TestCase):
    def test_default_emits_no_milestones(self):
        rules = rules_mod.apply_checklist(RULES, {})
        tasks, _stage = build_tasks(project(), {"kickoff": date(2026, 9, 10)}, rules, [], TODAY, SCHEDULE, level="L2")
        self.assertEqual([task.id for task in tasks if task.milestone], [])
        rows = by_id(tasks)
        self.assertEqual(rows["system_setup"].due, date(2027, 1, 29))
        self.assertEqual(rows["system_setup"].parent, "")
        self.assertTrue(rows["system_setup"].mandatory)

    def test_enabled_milestone_parents_steps(self):
        rules = rules_mod.apply_checklist(RULES, {"checklist_optional": ["ms_testing"]})
        tasks, _stage = build_tasks(project(), {"kickoff": date(2026, 9, 10)}, rules, [], TODAY, SCHEDULE, level="L2")
        rows = by_id(tasks)
        self.assertTrue(rows["ms_testing"].milestone)
        self.assertEqual(rows["system_setup"].parent, rows["ms_testing"].id)
        self.assertEqual(rows["system_setup"].due, date(2027, 1, 29))
        self.assertNotIn("ms_prefab", rows)

    def test_mandatory_ignores_levels_and_repeat(self):
        rules = rules_mod.apply_checklist(RULES, {})
        for level in ("L1", "L2R"):
            rows = by_id(build_tasks(project(), {"kickoff": date(2026, 9, 10)}, rules, [], TODAY, level=level)[0])
            self.assertIn("wiring_diagram", rows, level)
            self.assertIn("test_matrix", rows, level)

    def test_at_notification_dates_system_setup(self):
        rules = rules_mod.apply_checklist(RULES, {})
        rows = by_id(
            build_tasks(project(), {"at_notification": date(2027, 1, 4)}, rules, [], TODAY, level="L2")[0]
        )
        self.assertEqual(rows["system_setup"].due, date(2027, 1, 6))
        self.assertEqual(rows["system_setup"].due, rules_mod.add_business_days(date(2027, 1, 4), 2))
        self.assertEqual(rows["device_cables"].due, date(2027, 1, 11))
        self.assertEqual(rows["testing_demo"].due, date(2027, 1, 18))


class NoteRenderChecklistTests(unittest.TestCase):
    TEXT = (
        "**TO DO:**\n\n"
        "<!-- tracker:start (auto-managed; tick boxes freely) -->\n"
        "**Closeout**\n"
        "- [ ] UV reports posted \U0001F4C5 2026-11-20 \U0001F194 p0207001-co_uv\n"
        "- [x] Device wiki pages updated \U0001F4C5 2026-11-20 \u2705 2026-11-19 \U0001F194 p0207001-co_wiki\n"
        "<!-- tracker:end -->\n"
    )

    def test_unticked_optional_dropped_ticked_kept(self):
        text = notes.build_text(self.TEXT, {}, [], TODAY)
        self.assertNotIn("UV reports posted", text)
        self.assertIn("- [x] Device wiki pages updated", text)


class SettingsOverlayChecklistTests(unittest.TestCase):
    def test_apply_overlay_passes_checklist_optional(self):
        merged = settings_mod.apply_overlay({"vault": "C:/old"}, {"checklist_optional": ["fat", "fat", "ms_fat"]})
        self.assertEqual(merged["checklist_optional"], ["fat", "ms_fat"])
        self.assertEqual(merged["vault"], "C:/old")

    def test_apply_overlay_ignores_non_list(self):
        merged = settings_mod.apply_overlay({"vault": "C:/old"}, {"checklist_optional": "fat"})
        self.assertNotIn("checklist_optional", merged)


class HubChecklistTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.folder = self.vault / "Acme SO-1 0207000"
        self.folder.mkdir()
        self.note = self.folder / "Acme SO-1 0207000.md"
        self.note.write_text(NOTE_TEXT, encoding="utf-8")
        self.state_path = self.root / "state.json"
        self.snapshot_path = self.root / "snapshot.json"
        self.config_path = self.root / "config.yaml"
        self.cfg = {
            "vault": str(self.vault),
            "state_path": str(self.state_path),
            "snapshot_path": str(self.snapshot_path),
            "log_path": str(self.root / "last_run.log"),
            "hub": {"port": 8765, "refresh_minutes": 15, "toast": False},
        }
        self.config_path.write_text(yaml.safe_dump(self.cfg), encoding="utf-8")
        self.state_path.write_text(json.dumps({"ui": {}, "due_overrides": {}, "tick_log": []}), encoding="utf-8")
        self.snapshot_path.write_text(json.dumps(self.snapshot()), encoding="utf-8")
        self.runs = []
        self.app = create_app(
            self.config_path, port=8765, no_refresh=True, runner=lambda: self.runs.append(1) or 0
        )
        self.app.testing = True
        self.client = self.app.test_client()
        self.token = self.app.config["HUB_TOKEN"]

    def tearDown(self):
        self.tmp.cleanup()

    def snapshot(self) -> dict:
        return {
            "generated": "2026-11-10T06:30:00-08:00",
            "today": "2026-11-10",
            "projects": [
                {
                    "key": "0207000",
                    "name": "Acme SO-1 0207000",
                    "folder": str(self.folder),
                    "note_path": str(self.note),
                    "customer": "Acme",
                    "project_no": "0207000",
                    "so": [],
                    "qte": [],
                    "stage": "Pre-Fabrication",
                    "team": [],
                    "links": [],
                    "tasks": [
                        {
                            "id": "p0207000-time_estimate",
                            "title": "Time Estimate (in Project Page)",
                            "stage": "Pre-Fabrication",
                            "due": None,
                            "status": "open",
                            "mark": " ",
                            "source": "tracker",
                            "mandatory": True,
                        }
                    ],
                    "emails": [],
                }
            ],
            "pending": [],
            "unmatched": [],
        }

    def post(self, url: str, payload: dict):
        headers = dict(HOST)
        headers["X-Hub-Token"] = self.token
        headers["Origin"] = ORIGIN
        return self.client.post(url, json=payload, headers=headers)

    def get(self, url: str):
        return self.client.get(url, headers=dict(HOST))

    def overlay(self) -> dict:
        return json.loads((self.root / "settings.json").read_text(encoding="utf-8"))

    def test_settings_post_saves_and_reruns(self):
        response = self.post("/api/settings", {"checklist_optional": ["fat"]})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["rerun"])
        self.assertEqual(payload["settings"]["checklist_optional"], ["fat"])
        self.assertEqual(self.overlay()["checklist_optional"], ["fat"])
        self.assertEqual(self.runs, [1])

    def test_settings_post_rejects_unknown_and_bad_shape(self):
        response = self.post("/api/settings", {"checklist_optional": ["nope"]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "unknown checklist item: nope")
        response = self.post("/api/settings", {"checklist_optional": "fat"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "checklist_optional must be a list")
        self.assertEqual(self.runs, [])

    def test_settings_page_lists_checklist(self):
        html = self.get("/settings").get_data(as_text=True)
        self.assertIn("Project checklist", html)
        self.assertIn('data-id="time_estimate" checked disabled', html)
        self.assertIn('data-id="fat">', html)
        self.assertIn("Required only", html)

    def test_needs_date_flags_mandatory_task(self):
        html = self.get("/").get_data(as_text=True)
        self.assertIn("1 need date", html)
        self.assertIn("dot dot-warn", html)
        page = self.get("/p/0207000").get_data(as_text=True)
        self.assertIn("Needs date", page)


if __name__ == "__main__":
    unittest.main()
