from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tracker import notes
from tracker.hub import tasks
from tracker.hub.app import create_app
from tracker.models import Email, Project
from tracker.tracker import build_snapshot

TODAY = date(2026, 11, 11)
HOST = {"Host": "127.0.0.1:8765"}
ORIGIN = "http://127.0.0.1:8765"
PERSONAL = """# My tasks

- [ ] Buy milk \U0001f4c5 2026-11-20 \U0001f194 m-bbbb2222
- [ ] Fix SO-281090 pump \U0001f194 m-cccc3333
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


class ParseTimeTests(unittest.TestCase):
    def test_hours_days_ref_are_parsed_and_stripped(self):
        line = (
            "- [x] Send test plan \U0001f4c5 2026-11-12 \U0001f194 m-1a2b3c4d "
            "\u23f1 2h [days:: 2026-11-12, 2026-11-10] [ref:: so-281090]"
        )
        row = tasks.parse_lines(line + "\n")[0]
        self.assertEqual(row["title"], "Send test plan")
        self.assertEqual(row["hours"], 2.0)
        self.assertEqual(row["days"], [date(2026, 11, 10), date(2026, 11, 12)])
        self.assertEqual(row["ref"], "SO-281090")
        self.assertEqual(row["due"], date(2026, 11, 12))

    def test_absent_tokens(self):
        row = tasks.parse_lines("- [ ] Thing \U0001f194 m-1\n")[0]
        self.assertIsNone(row["hours"])
        self.assertEqual(row["days"], [])
        self.assertEqual(row["ref"], "")

    def test_variation_selector_and_bad_days(self):
        row = tasks.parse_lines("- [ ] Thing \u23f1\ufe0f 0.25h [days:: nope, 2026-11-10]\n")[0]
        self.assertEqual(row["hours"], 0.25)
        self.assertEqual(row["days"], [date(2026, 11, 10)])
        self.assertEqual(row["title"], "Thing")


class SetTimeTests(unittest.TestCase):
    def test_add_replace_remove(self):
        text = "- [x] Send test plan \U0001f4c5 2026-11-12 \U0001f194 m-1\n"
        added, row = tasks.set_time(
            text, task_id="m-1", hours=2, days=[date(2026, 11, 12), date(2026, 11, 10)]
        )
        self.assertEqual(
            added,
            "- [x] Send test plan \u23f1 2h [days:: 2026-11-10, 2026-11-12] "
            "\U0001f4c5 2026-11-12 \U0001f194 m-1\n",
        )
        self.assertEqual(row["hours"], 2.0)
        replaced, row = tasks.set_time(added, task_id="m-1", hours=1.5, days=[date(2026, 11, 11)])
        self.assertIn("\u23f1 1.5h [days:: 2026-11-11]", replaced)
        self.assertNotIn("2026-11-10", replaced)
        removed, row = tasks.set_time(replaced, task_id="m-1")
        self.assertEqual(removed, text)
        self.assertIsNone(row["hours"])

    def test_keeps_ref_token(self):
        text = "- [x] Thing [ref:: SO-281090] \U0001f194 m-1\n"
        new, _row = tasks.set_time(text, task_id="m-1", hours=0.25, days=[])
        self.assertEqual(new, "- [x] Thing \u23f1 0.25h [ref:: SO-281090] \U0001f194 m-1\n")

    def test_by_index_and_conflict(self):
        text = "- [x] Thing \U0001f194 m-1\n"
        new, _row = tasks.set_time(text, line_index=0, expected_line=text.strip(), hours=1)
        self.assertIn("\u23f1 1h", new)
        with self.assertRaises(ValueError):
            tasks.set_time(text, line_index=0, expected_line="gone", hours=1)
        with self.assertRaises(KeyError):
            tasks.set_time(text, task_id="m-9", hours=1)

    def test_bad_hours(self):
        text = "- [x] Thing \U0001f194 m-1\n"
        for value in (-1, 24 * 7 + 1, "abc"):
            with self.assertRaises(ValueError):
                tasks.set_time(text, task_id="m-1", hours=value)


class UpdateKeepsTimeTests(unittest.TestCase):
    def test_update_manual_keeps_hours_and_days(self):
        text = "- [ ] Buy parts \U0001f4c5 2026-11-20 \U0001f194 m-1 \u23f1 2h [days:: 2026-11-10]\n"
        new = tasks.update_manual(text, "m-1", description="Buy more parts")
        self.assertIn(
            "- [ ] Buy more parts \u23f1 2h [days:: 2026-11-10] \U0001f4c5 2026-11-20 \U0001f194 m-1",
            new,
        )

    def test_update_manual_sets_ref(self):
        text = "- [ ] Buy parts \U0001f194 m-1\n"
        new = tasks.update_manual(text, "m-1", ref="SO-281090")
        self.assertIn("[ref:: SO-281090]", new)
        cleared = tasks.update_manual(new, "m-1", ref="")
        self.assertNotIn("[ref::", cleared)


class SpawnTimeTests(unittest.TestCase):
    def test_spawn_drops_hours_keeps_ref(self):
        text = (
            "- [x] Enter timesheet \U0001f501 every week on Friday \U0001f4c5 2026-11-13 "
            "\U0001f194 m-1 \u23f1 2h [days:: 2026-11-13] [ref:: QTE-123456]\n"
        )
        new_text, spawned = tasks.spawn_next(text, "m-1", TODAY)
        self.assertIsNotNone(spawned)
        self.assertEqual(spawned["hours"], None)
        self.assertEqual(spawned["days"], [])
        self.assertEqual(spawned["ref"], "QTE-123456")
        self.assertNotIn("\u23f1", new_text.split("\n")[0])
        self.assertIn("[ref:: QTE-123456]", new_text.split("\n")[0])


class SuggestRefTests(unittest.TestCase):
    def test_prefers_so_then_qte(self):
        self.assertEqual(tasks.suggest_ref("Fix SO-281090 pump", ""), "SO-281090")
        self.assertEqual(tasks.suggest_ref("Quote QTE-123456", ""), "QTE-123456")
        self.assertEqual(tasks.suggest_ref("Fix pump", "see SO-281090"), "SO-281090")
        self.assertEqual(tasks.suggest_ref("nothing here", ""), "")


class TimeAppTests(unittest.TestCase):
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

    def test_time_on_personal_task(self):
        response = self.post(
            "/api/task/time",
            {"key": "", "id": "m-bbbb2222", "hours": 2, "days": [date.today().isoformat()]},
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["hours"], 2.0)
        self.assertEqual(body["days"], [date.today().isoformat()])
        self.assertIn("\u23f1 2h", body["line"])
        self.assertIn("\u23f1 2h", notes.read_text(self.personal)[0])

    def test_time_defaults_days_to_today(self):
        response = self.post("/api/task/time", {"key": "", "id": "m-bbbb2222", "hours": 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["days"], [date.today().isoformat()])

    def test_time_on_tracker_block_task_by_id(self):
        response = self.post(
            "/api/task/time",
            {"key": "0207000", "id": "p0207000-review_cpr_so", "hours": 1.5, "days": []},
        )
        self.assertEqual(response.status_code, 200)
        text = notes.read_text(self.note)[0]
        self.assertIn(
            "- [ ] Review CPR/SO \u23f1 1.5h [days:: 2026-11-12] \U0001f4c5 2026-11-02 \U0001f194 p0207000-review_cpr_so",
            text,
        )
        self.assertIn("<!-- tracker:end -->", text)

    def test_time_bad_hours(self):
        for value in ("abc", -1, 200):
            response = self.post("/api/task/time", {"key": "", "id": "m-bbbb2222", "hours": value})
            self.assertEqual(response.status_code, 400, value)
            self.assertEqual(response.get_json()["error"], "bad hours")

    def test_time_bad_days(self):
        response = self.post(
            "/api/task/time",
            {"key": "", "id": "m-bbbb2222", "hours": 1, "days": ["2026-13-01"]},
        )
        self.assertEqual(response.status_code, 400)
        old = (date.today() - timedelta(days=40)).isoformat()
        response = self.post("/api/task/time", {"key": "", "id": "m-bbbb2222", "hours": 1, "days": [old]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "bad days")

    def test_time_conflict(self):
        lines = notes.read_text(self.personal)[0].split("\n")
        index = next(i for i, line in enumerate(lines) if "m-bbbb2222" in line)
        response = self.post(
            "/api/task/time",
            {"key": "", "line_index": index, "expected_line": "gone", "hours": 1},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"], "conflict")

    def test_add_and_update_ref(self):
        added = self.post(
            "/api/tasks/add", {"key": "", "description": "Fix pump", "ref": "so-281090"}
        )
        self.assertEqual(added.status_code, 200)
        task_id = added.get_json()["id"]
        self.assertIn("[ref:: SO-281090]", notes.read_text(self.personal)[0])
        updated = self.post("/api/tasks/update", {"key": "", "id": task_id, "ref": ""})
        self.assertEqual(updated.status_code, 200)
        self.assertNotIn("[ref::", notes.read_text(self.personal)[0])

    def test_bad_ref(self):
        for value in ("ABC-1", "SO-", "ref"):
            response = self.post("/api/tasks/add", {"key": "", "description": "Fix pump", "ref": value})
            self.assertEqual(response.status_code, 400, value)
            self.assertEqual(response.get_json()["error"], "bad ref")
        response = self.post("/api/tasks/update", {"key": "", "id": "m-bbbb2222", "ref": "nope"})
        self.assertEqual(response.status_code, 400)

    def test_live_tasks_exposes_time_and_ref(self):
        self.post("/api/task/time", {"key": "", "id": "m-bbbb2222", "hours": 2, "days": []})
        self.post("/api/tasks/update", {"key": "", "id": "m-bbbb2222", "ref": "SO-281090"})
        items = self.client.get("/api/tasks", headers=HOST).get_json()["tasks"]
        row = next(item for item in items if item["id"] == "m-bbbb2222")
        self.assertEqual(row["hours"], 2.0)
        self.assertEqual(row["hours_label"], "2")
        self.assertEqual(row["worked_days"], [date.today().isoformat()])
        self.assertEqual(row["ref"], "SO-281090")
        other = next(item for item in items if item["id"] == "m-cccc3333")
        self.assertEqual(other["ref"], "")
        self.assertEqual(other["ref_suggested"], "SO-281090")


class SnapshotTimeTests(unittest.TestCase):
    def project(self) -> Project:
        return Project(
            folder="Acme SO-1 0207000",
            name="Acme SO-1 0207000",
            note_path="C:/vault/Acme.md",
            customer="Acme",
            project_no="0207000",
            so=["SO-281090"],
            qte=[],
            stage="Pre-Fabrication",
        )

    def test_calendar_minutes_and_start(self):
        tz = timezone.utc
        email = Email(
            message_id="calendar:uid:2026-11-10",
            subject="Kickoff",
            sender="pm@example.com",
            to="",
            cc="",
            date=datetime(2026, 11, 10, 9, 0, tzinfo=tz),
            body_text="",
            end=datetime(2026, 11, 10, 10, 30, tzinfo=tz),
        )
        snapshot = build_snapshot(
            TODAY,
            [self.project()],
            {},
            {"0207000": [(email, "kickoff", False)]},
            {},
            {},
            [],
            "2026-11-10T06:30:00-08:00",
        )
        entry = snapshot["projects"][0]["emails"][0]
        self.assertEqual(entry["minutes"], 90)
        self.assertIn("T", entry["start"])

    def test_calendar_minutes_zero_without_end(self):
        email = Email(
            message_id="calendar:uid:2026-11-10",
            subject="Kickoff",
            sender="",
            to="",
            cc="",
            date=datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc),
            body_text="",
        )
        snapshot = build_snapshot(
            TODAY,
            [self.project()],
            {},
            {"0207000": [(email, "kickoff", False)]},
            {},
            {},
            [],
            "2026-11-10T06:30:00-08:00",
        )
        self.assertEqual(snapshot["projects"][0]["emails"][0]["minutes"], 0)

    def test_unmatched_calendar_minutes(self):
        email = Email(
            message_id="calendar:uid:2026-11-10",
            subject="Kickoff",
            sender="",
            to="",
            cc="",
            date=datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc),
            body_text="",
            end=datetime(2026, 11, 10, 9, 45, tzinfo=timezone.utc),
        )
        snapshot = build_snapshot(
            TODAY,
            [],
            {},
            {},
            {},
            {},
            [
                {
                    "date": "2026-11-10",
                    "subject": "Kickoff",
                    "reason": "no project",
                    "message_id": email.message_id,
                    "keys": {},
                    "open": {},
                    "minutes": 45,
                }
            ],
            "2026-11-10T06:30:00-08:00",
        )
        self.assertEqual(snapshot["unmatched"][0]["minutes"], 45)

    def test_ape_sheet_index_picks_best_rank(self):
        sheet = {
            "ok": True,
            "my_initials": "AZ",
            "rows": [
                {
                    "project_id": "P-OLD",
                    "customer": "Acme",
                    "so": "281090",
                    "tab": "Closed",
                    "ae": "AZ",
                    "assigned": "2026-01-01",
                    "keys": {"so": ["SO-281090"], "qte": [], "project_no": ["0207000"]},
                },
                {
                    "project_id": "P-NEW",
                    "customer": "Acme",
                    "so": "281090",
                    "tab": "Active",
                    "ae": "AZ",
                    "assigned": "2026-06-01",
                    "keys": {"so": ["SO-281090"], "qte": ["QTE-123456"], "project_no": ["0207000"]},
                },
                {
                    "project_id": "",
                    "customer": "Nobody",
                    "so": "",
                    "tab": "Active",
                    "ae": "",
                    "assigned": "",
                    "keys": {"so": ["SO-999999"], "qte": [], "project_no": []},
                },
            ],
        }
        snapshot = build_snapshot(
            TODAY, [], {}, {}, {}, {}, [], "2026-11-10T06:30:00-08:00", sheet=sheet
        )
        index = snapshot["ape_sheet_index"]
        self.assertEqual(index["SO-281090"]["project_id"], "P-NEW")
        self.assertEqual(index["0207000"]["project_id"], "P-NEW")
        self.assertEqual(index["QTE-123456"]["tab"], "Active")
        self.assertEqual(index["SO-281090"]["customer"], "Acme")
        self.assertNotIn("SO-999999", index)

    def test_ape_sheet_index_empty_when_sheet_not_ok(self):
        snapshot = build_snapshot(
            TODAY, [], {}, {}, {}, {}, [], "2026-11-10T06:30:00-08:00", sheet={"ok": False, "rows": []}
        )
        self.assertEqual(snapshot["ape_sheet_index"], {})


if __name__ == "__main__":
    unittest.main()
