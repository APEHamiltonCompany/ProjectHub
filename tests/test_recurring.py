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
from tracker.hub import tasks
from tracker.hub.app import create_app

TODAY = date(2026, 11, 11)
HOST = {"Host": "127.0.0.1:8765"}
ORIGIN = "http://127.0.0.1:8765"
PERSONAL = """# My tasks

- [ ] Enter timesheet #admin \U0001f501 every week on Friday \U0001f4c5 2026-11-13 \U0001f194 m-aaaa1111
    - \U0001f4dd submit by noon
- [ ] Buy milk \U0001f4c5 2026-11-20 \U0001f194 m-bbbb2222
- [ ] Read spec #process-improvement \U0001f194 m-cccc3333
"""
NOTE_TEXT = """---
project_no: "0207000"
---

**My tasks**
- [ ] Buy parts \U0001f4c5 2026-11-20 \U0001f194 m-dddd4444

<!-- tracker:start (auto-managed; tick boxes freely) -->
**Pre-Fabrication**
- [ ] Review CPR/SO \U0001f4c5 2026-11-02 \U0001f194 p0207000-review_cpr_so
<!-- tracker:end -->
"""


class RuleParseTests(unittest.TestCase):
    def test_accepts_and_canonicalises(self):
        cases = {
            "every day": "every day",
            "Every Day": "every day",
            "every weekday": "every weekday",
            "every week on friday": "every week on Friday",
            "Every Week On FRIDAY": "every week on Friday",
            "every 2 weeks on monday": "every 2 weeks on Monday",
            "every 12 weeks on Sunday": "every 12 weeks on Sunday",
            "every month on the 1st": "every month on the 1st",
            "every month on the 2nd": "every month on the 2nd",
            "every month on the 3rd": "every month on the 3rd",
            "every month on the 11th": "every month on the 11th",
            "every month on the 13th": "every month on the 13th",
            "every month on the 21st": "every month on the 21st",
            "every month on the 22nd": "every month on the 22nd",
            "every month on the 23rd": "every month on the 23rd",
            "every month on the 31st": "every month on the 31st",
        }
        for text, expected in cases.items():
            self.assertEqual(tasks.canonical_rule(text), expected, text)

    def test_rejects(self):
        for text in (
            "",
            "every fortnight",
            "every 1 weeks on Monday",
            "every 13 weeks on Monday",
            "every month on the 32nd",
            "every month on the 0th",
            "every week on Funday",
            "weekly",
        ):
            self.assertIsNone(tasks.canonical_rule(text), text)
            self.assertIsNone(tasks.parse_rule(text), text)

    def test_parse_rule_shapes(self):
        self.assertEqual(tasks.parse_rule("every day"), {"kind": "day"})
        self.assertEqual(tasks.parse_rule("every weekday"), {"kind": "weekday"})
        self.assertEqual(tasks.parse_rule("every week on Friday"), {"kind": "week", "weeks": 1, "weekday": 4})
        self.assertEqual(tasks.parse_rule("every 3 weeks on Monday"), {"kind": "week", "weeks": 3, "weekday": 0})
        self.assertEqual(tasks.parse_rule("every month on the 15th"), {"kind": "month", "day": 15})

    def test_short_label(self):
        self.assertEqual(tasks.short_label("every day"), "daily")
        self.assertEqual(tasks.short_label("every weekday"), "weekdays")
        self.assertEqual(tasks.short_label("every week on Friday"), "weekly")
        self.assertEqual(tasks.short_label("every 3 weeks on Monday"), "every 3 weeks")
        self.assertEqual(tasks.short_label("every month on the 15th"), "monthly")
        self.assertEqual(tasks.short_label(""), "")


class NextDueTests(unittest.TestCase):
    def test_daily(self):
        self.assertEqual(tasks.next_due("every day", date(2026, 11, 11), TODAY), date(2026, 11, 12))
        self.assertEqual(tasks.next_due("every day", date(2026, 11, 1), TODAY), date(2026, 11, 11))

    def test_weekday_skips_weekend(self):
        friday = date(2026, 11, 13)
        self.assertEqual(tasks.next_due("every weekday", friday, friday), date(2026, 11, 16))
        self.assertEqual(tasks.next_due("every weekday", date(2026, 11, 12), TODAY), date(2026, 11, 13))

    def test_weekly(self):
        self.assertEqual(
            tasks.next_due("every week on Friday", date(2026, 11, 13), TODAY), date(2026, 11, 20)
        )
        self.assertEqual(
            tasks.next_due("every week on Friday", date(2026, 11, 6), TODAY), date(2026, 11, 13)
        )
        self.assertEqual(
            tasks.next_due("every week on Friday", date(2026, 10, 2), TODAY), date(2026, 11, 13)
        )

    def test_two_weekly(self):
        self.assertEqual(
            tasks.next_due("every 2 weeks on Friday", date(2026, 11, 13), TODAY), date(2026, 11, 27)
        )
        self.assertEqual(
            tasks.next_due("every 2 weeks on Friday", date(2026, 10, 30), TODAY), date(2026, 11, 13)
        )
        self.assertEqual(
            tasks.next_due("every 2 weeks on Friday", date(2026, 11, 11), TODAY), date(2026, 11, 13)
        )

    def test_monthly_clamps_short_months(self):
        self.assertEqual(
            tasks.next_due("every month on the 31st", date(2027, 1, 31), date(2027, 1, 31)),
            date(2027, 2, 28),
        )
        self.assertEqual(
            tasks.next_due("every month on the 31st", date(2028, 1, 31), date(2028, 1, 31)),
            date(2028, 2, 29),
        )
        self.assertEqual(
            tasks.next_due("every month on the 15th", date(2026, 11, 15), date(2026, 12, 20)),
            date(2027, 1, 15),
        )

    def test_first_due(self):
        self.assertEqual(tasks.first_due("every day", TODAY), TODAY)
        self.assertEqual(tasks.first_due("every week on Friday", TODAY), date(2026, 11, 13))
        self.assertEqual(tasks.first_due("every weekday", date(2026, 11, 14)), date(2026, 11, 16))
        self.assertEqual(tasks.first_due("every month on the 15th", TODAY), date(2026, 11, 15))
        self.assertEqual(tasks.first_due("every month on the 15th", date(2026, 11, 20)), date(2026, 12, 15))

    def test_bad_rule_raises(self):
        with self.assertRaises(ValueError):
            tasks.next_due("every fortnight", TODAY, TODAY)
        with self.assertRaises(ValueError):
            tasks.first_due("every fortnight", TODAY)


class ParseLineTests(unittest.TestCase):
    def test_tag_and_rule_are_stripped_from_title(self):
        rows = tasks.parse_lines(PERSONAL)
        self.assertEqual(len(rows), 3)
        first = rows[0]
        self.assertEqual(first["title"], "Enter timesheet")
        self.assertEqual(first["category"], "admin")
        self.assertEqual(first["recur"], "every week on Friday")
        self.assertEqual(first["due"], date(2026, 11, 13))
        self.assertEqual(first["notes"], "submit by noon")
        second = rows[1]
        self.assertEqual(second["title"], "Buy milk")
        self.assertEqual(second["category"], "")
        self.assertEqual(second["recur"], "")
        third = rows[2]
        self.assertEqual(third["title"], "Read spec")
        self.assertEqual(third["category"], "process-improvement")
        self.assertIsNone(third["due"])

    def test_rule_without_due(self):
        rows = tasks.parse_lines("- [ ] Thing \U0001f501 every 2 weeks on Monday \U0001f194 m-1\n")
        self.assertEqual(rows[0]["title"], "Thing")
        self.assertEqual(rows[0]["recur"], "every 2 weeks on Monday")
        self.assertIsNone(rows[0]["due"])

    def test_unknown_rule_is_kept_out_of_recur(self):
        rows = tasks.parse_lines("- [ ] Thing \U0001f501 every fortnight \U0001f194 m-1\n")
        self.assertEqual(rows[0]["recur"], "")
        self.assertEqual(rows[0]["title"], "Thing")


class LineBodyTests(unittest.TestCase):
    def test_order(self):
        body = tasks._line_body(
            "Enter timesheet", date(2026, 11, 13), "m-1", category="admin", recur="every week on Friday"
        )
        self.assertEqual(
            body,
            "Enter timesheet #admin \U0001f501 every week on Friday \U0001f4c5 2026-11-13 \U0001f194 m-1",
        )

    def test_project_category_writes_no_tag(self):
        body = tasks._line_body("Buy parts", None, "m-2", category="project")
        self.assertEqual(body, "Buy parts \U0001f194 m-2")

    def test_manual_line_round_trip(self):
        line = tasks.manual_line(
            "Enter timesheet", date(2026, 11, 13), "m-1", category="admin", recur="every week on Friday"
        )
        self.assertEqual(
            line,
            "- [ ] Enter timesheet #admin \U0001f501 every week on Friday \U0001f4c5 2026-11-13 \U0001f194 m-1",
        )

    def test_update_preserves_tag_and_rule(self):
        new = tasks.update_manual(PERSONAL, "m-aaaa1111", description="Enter weekly timesheet")
        self.assertIn(
            "- [ ] Enter weekly timesheet #admin \U0001f501 every week on Friday "
            "\U0001f4c5 2026-11-13 \U0001f194 m-aaaa1111",
            new,
        )
        self.assertIn("submit by noon", new)

    def test_update_removes_rule(self):
        new = tasks.update_manual(PERSONAL, "m-aaaa1111", recur="")
        self.assertNotIn("\U0001f501", new)
        self.assertIn("- [ ] Enter timesheet #admin \U0001f4c5 2026-11-13 \U0001f194 m-aaaa1111", new)

    def test_update_changes_category(self):
        new = tasks.update_manual(PERSONAL, "m-aaaa1111", category="other")
        self.assertIn("#other", new)
        self.assertNotIn("#admin", new)
        rows = tasks.parse_lines(new)
        self.assertEqual(rows[0]["category"], "other")
        self.assertEqual(rows[0]["recur"], "every week on Friday")


class SpawnTests(unittest.TestCase):
    def test_spawns_above_completed_line(self):
        text, _row = tasks.set_mark(PERSONAL, task_id="m-aaaa1111", mark="x")
        new_text, spawned = tasks.spawn_next(text, "m-aaaa1111", TODAY)
        self.assertIsNotNone(spawned)
        self.assertEqual(spawned["due"], date(2026, 11, 20))
        self.assertEqual(spawned["recur"], "every week on Friday")
        self.assertEqual(spawned["category"], "admin")
        self.assertEqual(spawned["mark"], " ")
        lines = new_text.split("\n")
        ticked = next(i for i, line in enumerate(lines) if "m-aaaa1111" in line)
        self.assertIn("[x] Enter timesheet", lines[ticked])
        self.assertIn("[ ] Enter timesheet", lines[ticked - 2])
        self.assertIn("2026-11-20", lines[ticked - 2])
        self.assertIn("submit by noon", lines[ticked - 1])
        self.assertIn("submit by noon", lines[ticked + 1])
        self.assertIn("[ ] Buy milk", new_text)

    def test_no_spawn_for_open_task(self):
        new_text, spawned = tasks.spawn_next(PERSONAL, "m-aaaa1111", TODAY)
        self.assertIsNone(spawned)
        self.assertEqual(new_text, PERSONAL)

    def test_no_spawn_without_rule(self):
        text, _row = tasks.set_mark(PERSONAL, task_id="m-bbbb2222", mark="x")
        new_text, spawned = tasks.spawn_next(text, "m-bbbb2222", TODAY)
        self.assertIsNone(spawned)
        self.assertEqual(new_text, text)

    def test_no_duplicate(self):
        text, _row = tasks.set_mark(PERSONAL, task_id="m-aaaa1111", mark="x")
        once, spawned = tasks.spawn_next(text, "m-aaaa1111", TODAY)
        self.assertIsNotNone(spawned)
        twice, again = tasks.spawn_next(once, "m-aaaa1111", TODAY)
        self.assertIsNone(again)
        self.assertEqual(once, twice)

    def test_na_mark_spawns(self):
        text, _row = tasks.set_mark(PERSONAL, task_id="m-aaaa1111", mark="-")
        new_text, spawned = tasks.spawn_next(text, "m-aaaa1111", TODAY)
        self.assertIsNotNone(spawned)
        self.assertIn("[-] Enter timesheet", new_text)


class RecurringAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.folder = self.vault / "Acme SO-1 0207000"
        self.folder.mkdir()
        self.note = self.folder / "Acme SO-1 0207000.md"
        self.note.write_text(NOTE_TEXT, encoding="utf-8")
        self.personal = self.vault / "My Tasks.md"
        self.personal.write_text(PERSONAL, encoding="utf-8")
        self.state_path = self.root / "state.json"
        self.snapshot_path = self.root / "snapshot.json"
        self.config_path = self.root / "config.yaml"
        cfg = {
            "vault": str(self.vault),
            "state_path": str(self.state_path),
            "snapshot_path": str(self.snapshot_path),
            "log_path": str(self.root / "last_run.log"),
            "templates_dir": str(self.root / "templates"),
            "rules_path": str(ROOT / "tracker" / "rules.yaml"),
            "hub": {"port": 8765, "refresh_minutes": 15, "toast": False},
        }
        self.config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        self.state_path.write_text(json.dumps({"tick_log": [], "due_overrides": {}}), encoding="utf-8")
        self.snapshot_path.write_text(
            json.dumps(
                {
                    "generated": "2026-11-10T06:30:00-08:00",
                    "projects": [
                        {
                            "key": "0207000",
                            "name": "Acme SO-1 0207000",
                            "folder": str(self.folder),
                            "note_path": str(self.note),
                            "customer": "Acme",
                            "project_no": "0207000",
                            "so": [],
                            "qte": ["QTE-1"],
                            "stage": "Pre-Fabrication",
                            "tasks": [],
                            "emails": [],
                        }
                    ],
                    "pending": [],
                    "unmatched": [],
                }
            ),
            encoding="utf-8",
        )
        self.app = create_app(self.config_path, port=8765, no_refresh=True, runner=lambda: 0)
        self.app.testing = True
        self.client = self.app.test_client()
        self.token = self.app.config["HUB_TOKEN"]

    def tearDown(self):
        self.tmp.cleanup()

    def post(self, url: str, payload: dict):
        headers = dict(HOST)
        headers["X-Hub-Token"] = self.token
        headers["Origin"] = ORIGIN
        return self.client.post(url, json=payload, headers=headers)

    def test_untagged_personal_line_is_personal_category(self):
        items = self.client.get("/api/tasks", headers=HOST).get_json()["tasks"]
        rows = {item["id"]: item for item in items if item["key"] == ""}
        self.assertEqual(rows["m-bbbb2222"]["category"], "personal")
        self.assertEqual(rows["m-bbbb2222"]["category_label"], "Personal")
        self.assertEqual(rows["m-bbbb2222"]["stage"], "Personal")
        self.assertEqual(rows["m-aaaa1111"]["category"], "admin")
        self.assertEqual(rows["m-aaaa1111"]["stage"], "Admin")
        self.assertEqual(rows["m-aaaa1111"]["recur"], "every week on Friday")
        self.assertEqual(rows["m-aaaa1111"]["recur_label"], "weekly")
        self.assertEqual(rows["m-cccc3333"]["category"], "process-improvement")

    def test_project_task_is_project_category(self):
        items = self.client.get("/api/tasks", headers=HOST).get_json()["tasks"]
        rows = [item for item in items if item["id"] == "m-dddd4444"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["category"], "project")
        self.assertEqual(rows[0]["category_label"], "Project")
        self.assertEqual(rows[0]["recur"], "")

    def test_add_admin_recurring_task(self):
        response = self.post(
            "/api/tasks/add",
            {
                "category": "admin",
                "description": "Enter timesheet",
                "due": "2026-11-13",
                "repeat": "every week on Friday",
            },
        )
        self.assertEqual(response.status_code, 200)
        task_id = response.get_json()["id"]
        self.assertEqual(response.get_json()["uid"], ":" + task_id)
        text = notes.read_text(self.personal)[0]
        self.assertIn(
            "- [ ] Enter timesheet #admin \U0001f501 every week on Friday "
            "\U0001f4c5 2026-11-13 \U0001f194 " + task_id,
            text,
        )

    def test_add_recurring_without_due_uses_first_due(self):
        response = self.post(
            "/api/tasks/add",
            {"category": "admin", "description": "Enter timesheet", "repeat": "every week on Friday"},
        )
        self.assertEqual(response.status_code, 200)
        expected = tasks.first_due("every week on Friday", date.today()).isoformat()
        self.assertIn(expected, notes.read_text(self.personal)[0])

    def test_add_project_category_requires_key(self):
        response = self.post("/api/tasks/add", {"category": "project", "description": "Buy parts"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "choose a project")

    def test_add_rejects_bad_category_and_repeat(self):
        self.assertEqual(
            self.post("/api/tasks/add", {"category": "bogus", "description": "x"}).status_code, 400
        )
        self.assertEqual(
            self.post(
                "/api/tasks/add", {"category": "admin", "description": "x", "repeat": "every fortnight"}
            ).status_code,
            400,
        )
        self.assertEqual(
            self.post(
                "/api/tasks/add", {"category": "admin", "description": "x #admin"}
            ).status_code,
            400,
        )
        self.assertEqual(
            self.post(
                "/api/tasks/add", {"category": "admin", "description": "x \U0001f501 every day"}
            ).status_code,
            400,
        )

    def test_update_category_and_repeat(self):
        response = self.post(
            "/api/tasks/update",
            {"key": "", "id": "m-bbbb2222", "category": "other", "repeat": "every month on the 20th"},
        )
        self.assertEqual(response.status_code, 200)
        text = notes.read_text(self.personal)[0]
        self.assertIn(
            "- [ ] Buy milk #other \U0001f501 every month on the 20th \U0001f4c5 2026-11-20 \U0001f194 m-bbbb2222",
            text,
        )
        removed = self.post("/api/tasks/update", {"key": "", "id": "m-bbbb2222", "repeat": ""})
        self.assertEqual(removed.status_code, 200)
        text = notes.read_text(self.personal)[0]
        self.assertIn("- [ ] Buy milk #other \U0001f4c5 2026-11-20 \U0001f194 m-bbbb2222", text)

    def test_update_category_rejected_for_project_task(self):
        response = self.post(
            "/api/tasks/update", {"key": "0207000", "id": "m-dddd4444", "category": "admin"}
        )
        self.assertEqual(response.status_code, 400)
        response = self.post("/api/tasks/update", {"key": "", "id": "m-bbbb2222", "category": "project"})
        self.assertEqual(response.status_code, 400)

    def test_toggle_spawns_next_instance(self):
        before = notes.read_text(self.personal)[0]
        response = self.post("/api/task/toggle", {"key": "", "id": "m-aaaa1111", "mark": "x"})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["mark"], "x")
        self.assertIn("[x] Enter timesheet", body["line"])
        spawned = body["spawned"]
        self.assertIsNotNone(spawned)
        self.assertTrue(spawned["id"].startswith("m-"))
        self.assertNotEqual(spawned["id"], "m-aaaa1111")
        self.assertEqual(spawned["due"], "2026-11-20")
        text = notes.read_text(self.personal)[0]
        lines = text.split("\n")
        ticked = next(i for i, line in enumerate(lines) if "m-aaaa1111" in line)
        self.assertIn("[x] Enter timesheet", lines[ticked])
        self.assertIn(spawned["id"], lines[ticked - 2])
        self.assertIn("2026-11-20", lines[ticked - 2])
        self.assertIn("[ ] Buy milk", text)
        self.assertIn("[ ] Read spec #process-improvement", text)
        self.assertEqual(len(text), len(before) + len(lines[ticked - 2]) + len(lines[ticked - 1]) + 2)
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(len(state["tick_log"]), 1)
        self.assertEqual(state["tick_log"][0]["task_id"], "m-aaaa1111")

    def test_toggle_twice_does_not_duplicate(self):
        self.post("/api/task/toggle", {"key": "", "id": "m-aaaa1111", "mark": "x"})
        self.post("/api/task/toggle", {"key": "", "id": "m-aaaa1111", "mark": " "})
        again = self.post("/api/task/toggle", {"key": "", "id": "m-aaaa1111", "mark": "x"})
        self.assertEqual(again.status_code, 200)
        self.assertIsNone(again.get_json()["spawned"])
        text = notes.read_text(self.personal)[0]
        self.assertEqual(text.count("Enter timesheet"), 2)

    def test_toggle_non_recurring_has_no_spawn(self):
        response = self.post("/api/task/toggle", {"key": "", "id": "m-bbbb2222", "mark": "x"})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.get_json()["spawned"])

    def test_dashboard_groups_and_table_category(self):
        page = self.client.get("/", headers=HOST).get_data(as_text=True)
        self.assertIn("task-group-head", page)
        self.assertIn("Process improvement", page)
        self.assertIn("tag-cat", page)
        self.assertIn("tag-recur", page)
        self.assertIn('data-category="admin"', page)
        self.assertIn('data-category="project"', page)
        self.assertIn('id="task-category"', page)
        self.assertIn('id="nt-category"', page)
        self.assertIn('id="nt-repeat"', page)
        self.assertIn('<option value="admin" selected>', page)
        self.assertIn('<option value="week" selected>', page)
        self.assertIn('<option value="Friday" selected>', page)

    def test_monday_labels_non_project_group(self):
        page = self.client.get("/monday", headers=HOST).get_data(as_text=True)
        self.assertIn("Non-project tasks", page)
        self.assertIn("[Admin] Enter timesheet", page)


if __name__ == "__main__":
    unittest.main()
