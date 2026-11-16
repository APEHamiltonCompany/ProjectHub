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
from tracker.hub import timesheet
from tracker.hub.app import create_app
from tracker.models import Task
from tests.test_hub import HOST, ORIGIN

NOTE_TEXT = """---
project_no: "0207000"
so: []
qte: ["QTE-1"]
customer: "Acme"
level: "L1"
stage: "Pre-Fabrication"
assigned: "2026-11-02"
sharepoint: "https://example.com/sp"
cpr: ""
rmt: ""
ship_date: "2026-12-01"
design_need_by: ""
team: ["A B"]
---

**TO DO:** (Mark done items with #)

<!-- tracker:start (auto-managed; tick boxes freely) -->
**Pre-Fabrication**
- [ ] Review CPR/SO \U0001F4C5 2026-11-02 \U0001F194 p0207000-review_cpr_so
<!-- tracker:end -->
"""

PERSONAL_TEXT = """# My tasks

- [ ] Buy parts \U0001F4C5 2026-11-20 \U0001F194 m-aaaa1111
"""


class TaskTimeTrackTests(unittest.TestCase):
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
        self.personal.write_text(PERSONAL_TEXT, encoding="utf-8")
        self.rules_path = self.root / "rules.yaml"
        self.rules_path.write_text(
            yaml.safe_dump(
                {
                    "stages": ["Pre-Fabrication", "System Test", "Closeout", "Completion"],
                    "workflow": [
                        {
                            "id": "p0207000-review_cpr_so",
                            "stage": "Pre-Fabrication",
                            "title": "Review CPR/SO",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.state_path = self.root / "state.json"
        self.snapshot_path = self.root / "snapshot.json"
        self.config_path = self.root / "config.yaml"
        self.cfg = {
            "vault": str(self.vault),
            "exclude_folders": ["General"],
            "state_path": str(self.state_path),
            "snapshot_path": str(self.snapshot_path),
            "log_path": str(self.root / "last_run.log"),
            "backup_root": str(self.root / "backups"),
            "templates_dir": str(self.root / "templates"),
            "rules_path": str(self.rules_path),
            "me": {"name": "Anthony Zunino"},
            "hub": {"port": 8765, "refresh_minutes": 15, "toast": False},
        }
        self.config_path.write_text(yaml.safe_dump(self.cfg), encoding="utf-8")
        self.state_path.write_text(
            json.dumps({"pending": {}, "unmatched": [], "email_overrides": {}, "due_overrides": {}}),
            encoding="utf-8",
        )
        self.snapshot_path.write_text(json.dumps(self.snapshot()), encoding="utf-8")
        self.app = create_app(self.config_path, port=8765, no_refresh=True, runner=lambda: 0)
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
                    "qte": ["QTE-1"],
                    "level": "L1",
                    "stage": "Pre-Fabrication",
                    "assigned": "2026-11-02",
                    "ship_date": "2026-12-01",
                    "design_need_by": "",
                    "sharepoint": "https://example.com/sp",
                    "cpr": "",
                    "rmt": "",
                    "team": ["A B"],
                    "links": [],
                    "triggers": {},
                    "tasks": [
                        {
                            "id": "p0207000-review_cpr_so",
                            "title": "Review CPR/SO",
                            "stage": "Pre-Fabrication",
                            "due": "2026-11-02",
                            "status": "open",
                            "mark": " ",
                            "source": "tracker",
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

    def text_of(self, path: Path) -> str:
        return notes.read_text(path)[0]

    def test_add_and_remove_entries_on_personal_task(self):
        day = date.today().isoformat()
        first = self.post(
            "/api/task/time/add", {"key": "", "id": "m-aaaa1111", "hours": 1.5, "day": day}
        )
        self.assertEqual(first.status_code, 200)
        payload = first.get_json()
        self.assertEqual(payload["time"], [{"day": day, "hours": 1.5}])
        self.assertEqual(payload["tracked"], 1.5)
        self.assertIn("[time:: %s=1.5]" % day, payload["line"])
        self.assertIn("- [ ] Buy parts", self.text_of(self.personal))

        second = self.post(
            "/api/task/time/add", {"key": "", "id": "m-aaaa1111", "hours": 2, "day": day}
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.get_json()["tracked"], 3.5)
        self.assertEqual(len(second.get_json()["time"]), 2)
        self.assertIn("[time:: %s=1.5, %s=2]" % (day, day), second.get_json()["line"])

        removed = self.post(
            "/api/task/time/remove", {"key": "", "id": "m-aaaa1111", "entry_index": 0}
        )
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(removed.get_json()["tracked"], 2.0)
        self.assertEqual(removed.get_json()["time"], [{"day": day, "hours": 2.0}])

        cleared = self.post(
            "/api/task/time/remove", {"key": "", "id": "m-aaaa1111", "entry_index": 0}
        )
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(cleared.get_json()["tracked"], 0)
        self.assertEqual(cleared.get_json()["time"], [])
        self.assertNotIn("[time::", cleared.get_json()["line"])
        self.assertIn("- [ ] Buy parts", self.text_of(self.personal))

    def test_add_and_remove_entries_on_workflow_task(self):
        day = date.today().isoformat()
        added = self.post(
            "/api/task/time/add",
            {"key": "0207000", "id": "p0207000-review_cpr_so", "hours": 0.5, "day": day},
        )
        self.assertEqual(added.status_code, 200)
        self.assertEqual(added.get_json()["tracked"], 0.5)
        text = self.text_of(self.note)
        self.assertIn("- [ ] Review CPR/SO [time:: %s=0.5]" % day, text)
        self.assertIn("\U0001F4C5 2026-11-02", text)

        removed = self.post(
            "/api/task/time/remove",
            {"key": "0207000", "id": "p0207000-review_cpr_so", "entry_index": 0},
        )
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(removed.get_json()["tracked"], 0)
        self.assertNotIn("[time::", self.text_of(self.note))

    def test_add_by_line_index_and_conflict(self):
        day = date.today().isoformat()
        line = "- [ ] Buy parts \U0001F4C5 2026-11-20 \U0001F194 m-aaaa1111"
        ok = self.post(
            "/api/task/time/add",
            {"key": "", "line_index": 2, "expected_line": line, "hours": 1, "day": day},
        )
        self.assertEqual(ok.status_code, 200)
        stale = self.post(
            "/api/task/time/add",
            {"key": "", "line_index": 2, "expected_line": line, "hours": 1, "day": day},
        )
        self.assertEqual(stale.status_code, 409)

    def test_bad_hours_and_missing_note(self):
        day = date.today().isoformat()
        for value in (0, -1, 25, "abc"):
            response = self.post(
                "/api/task/time/add", {"key": "", "id": "m-aaaa1111", "hours": value, "day": day}
            )
            self.assertEqual(response.status_code, 400)
        self.assertEqual(
            self.post(
                "/api/task/time/add", {"key": "nope", "id": "m-aaaa1111", "hours": 1, "day": day}
            ).status_code,
            404,
        )
        self.assertEqual(
            self.post(
                "/api/task/time/remove", {"key": "", "id": "m-aaaa1111", "entry_index": 0}
            ).status_code,
            409,
        )

    def test_project_page_renders_track_button(self):
        page = self.client.get("/p/0207000", headers=HOST).get_data(as_text=True)
        self.assertIn('data-action="time-track"', page)
        self.assertIn('data-id="p0207000-review_cpr_so"', page)
        self.post(
            "/api/task/time/add",
            {
                "key": "0207000",
                "id": "p0207000-review_cpr_so",
                "hours": 1,
                "day": date.today().isoformat(),
            },
        )
        page = self.client.get("/p/0207000", headers=HOST).get_data(as_text=True)
        self.assertIn('data-action="time-entries"', page)

    def test_set_time_keeps_time_entries(self):
        text = "- [x] Thing [time:: 2026-11-12=1.5] \U0001F194 m-1\n"
        new, _row = tasks.set_time(text, task_id="m-1", hours=2, days=[date(2026, 11, 12)])
        self.assertIn("[time:: 2026-11-12=1.5]", new)
        self.assertIn("\u23f1 2h", new)

    def test_parse_strips_time_token_from_title(self):
        row = tasks.parse_lines(
            "- [ ] Buy parts [time:: 2026-11-12=1.5, 2026-11-13=2] \U0001F4C5 2026-11-20 "
            "\U0001F194 m-1\n"
        )[0]
        self.assertEqual(row["title"], "Buy parts")
        self.assertEqual(
            row["time"], [(date(2026, 11, 12), 1.5), (date(2026, 11, 13), 2.0)]
        )
        self.assertEqual(row["due"], date(2026, 11, 20))

    def test_spawn_next_drops_time_entries(self):
        text = (
            "- [x] Weekly report \U0001F501 every week on Monday \U0001F4C5 2026-11-09 "
            "\U0001F194 m-1 [time:: 2026-11-09=2]\n"
        )
        new_text, row = tasks.spawn_next(text, "m-1", date(2026, 11, 16))
        self.assertIsNotNone(row)
        self.assertIn("- [ ] Weekly report", new_text)
        self.assertNotIn("[time::", row["line"])

    def test_render_block_keeps_time_on_open_line(self):
        path = self.root / "render.md"
        path.write_text(
            NOTE_TEXT.replace(
                "- [ ] Review CPR/SO \U0001F4C5 2026-11-02 \U0001F194 p0207000-review_cpr_so",
                "- [ ] Review CPR/SO [time:: 2026-11-12=1.5] \U0001F4C5 2026-11-02 "
                "\U0001F194 p0207000-review_cpr_so",
            ),
            encoding="utf-8",
        )
        task = Task(
            id="p0207000-review_cpr_so",
            title="Review CPR/SO",
            stage="Pre-Fabrication",
            due=date(2026, 11, 2),
        )
        changed, _diff = notes.apply_update(path, {}, [task], date(2026, 11, 13))
        self.assertFalse(changed)
        text = notes.read_text(path)[0]
        self.assertIn(
            "- [ ] Review CPR/SO [time:: 2026-11-12=1.5] \U0001F4C5 2026-11-02 "
            "\U0001F194 p0207000-review_cpr_so",
            text,
        )
        entries, user_lines = notes.parse_block(notes.split_frontmatter(text)[2])
        block = notes.render_block([task], entries, user_lines, date(2026, 11, 13))
        self.assertIn(
            "- [ ] Review CPR/SO [time:: 2026-11-12=1.5] \U0001F4C5 2026-11-02 "
            "\U0001F194 p0207000-review_cpr_so",
            "\n".join(block),
        )

    def test_timesheet_counts_open_task_entries(self):
        items = [
            {
                "key": "0207000",
                "ref": "",
                "mark": " ",
                "title": "Review CPR/SO",
                "time_entries": [
                    {"day": "2026-11-11", "hours": 1.5},
                    {"day": "2026-11-12", "hours": 2.0},
                ],
            }
        ]
        data = timesheet.build(
            items, self.snapshot(), [], {}, date(2026, 11, 10), date(2026, 11, 16)
        )
        row = data["rows"][0]
        self.assertEqual(row["cells"]["2026-11-11"]["hours"], 1.5)
        self.assertEqual(row["cells"]["2026-11-12"]["hours"], 2.0)
        self.assertEqual(row["total"], 3.5)
        self.assertIn("task: Review CPR/SO (1.5h, open)", row["cells"]["2026-11-11"]["sources"])
        self.assertEqual(row["tasks"], [{"title": "Review CPR/SO", "hours": 3.5, "days": ["2026-11-11", "2026-11-12"]}])

    def test_timesheet_counts_tracked_plus_completion_extra(self):
        items = [
            {
                "key": "0207000",
                "ref": "",
                "mark": "x",
                "title": "Review CPR/SO",
                "hours": 1.0,
                "worked_days": ["2026-11-12"],
                "time_entries": [{"day": "2026-11-11", "hours": 2.0}],
            }
        ]
        data = timesheet.build(
            items, self.snapshot(), [], {}, date(2026, 11, 10), date(2026, 11, 16)
        )
        row = data["rows"][0]
        self.assertEqual(row["cells"]["2026-11-11"]["hours"], 2.0)
        self.assertEqual(row["cells"]["2026-11-12"]["hours"], 1.0)
        self.assertEqual(row["total"], 3.0)
        self.assertIn("task: Review CPR/SO (2h, done)", row["cells"]["2026-11-11"]["sources"])


if __name__ == "__main__":
    unittest.main()
