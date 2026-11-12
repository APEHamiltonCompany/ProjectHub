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

from tracker.hub import timesheet
from tracker.hub.app import create_app
from tests.test_hub import HOST, ORIGIN

START = date(2026, 11, 9)
END = date(2026, 11, 15)
NOTE_TEXT = """---
project_no: "0207000"
so: ["SO-281090"]
qte: []
customer: "Acme"
level: "L1"
stage: "Pre-Fabrication"
assigned: "2026-11-02"
ship_date: "2026-12-01"
---

**TO DO:** (Mark done items with #)

<!-- tracker:start (auto-managed; tick boxes freely) -->
**Pre-Fabrication**
- [x] Send test plan \u23f1 4h [days:: 2026-11-10, 2026-11-12] \U0001F4C5 2026-11-12 \U0001F194 p0207000-send_test_plan
<!-- tracker:end -->
"""


def item(**kwargs) -> dict:
    row = {
        "key": "",
        "project_name": "",
        "customer": "",
        "ident": "",
        "kind": "workflow",
        "id": "t-1",
        "title": "Task",
        "mark": "x",
        "category": "project",
        "hours": None,
        "worked_days": [],
        "ref": "",
    }
    row.update(kwargs)
    return row


def snapshot(meetings: bool = True) -> dict:
    emails = [
        {
            "date": "2026-11-10T09:00:00-08:00",
            "subject": "Acme kickoff",
            "message_id": "msg-1",
            "trigger": "kickoff",
        }
    ]
    if meetings:
        emails.append(
            {
                "date": "2026-11-10T09:00:00-08:00",
                "start": "2026-11-10T09:00:00-08:00",
                "subject": "Kickoff call",
                "message_id": "calendar:uid:2026-11-10",
                "minutes": 90,
            }
        )
    return {
        "projects": [
            {
                "key": "0207000",
                "name": "Acme SO-1 0207000",
                "customer": "Acme",
                "project_no": "0207000",
                "so": ["SO-281090"],
                "qte": [],
                "sheet": {
                    "project_id": "PRJ-009134",
                    "customer": "Acme",
                    "so": "281090",
                    "tab": "Active",
                },
                "emails": emails,
            },
            {
                "key": "0208000",
                "name": "Beta SO-2 0208000",
                "customer": "Beta",
                "project_no": "0208000",
                "so": ["SO-295546"],
                "qte": [],
                "sheet": None,
                "emails": [],
            },
        ],
        "ape_sheet_index": {
            "SO-295546": {
                "project_id": "PRJ-009999",
                "customer": "Beta",
                "so": "295546",
                "tab": "Active",
            }
        },
    }


class BuildTests(unittest.TestCase):
    def test_project_task_hours_split_over_days(self):
        data = timesheet.build(
            [
                item(
                    key="0207000",
                    title="Send test plan",
                    hours=4.0,
                    worked_days=["2026-11-10", "2026-11-12"],
                )
            ],
            snapshot(meetings=False),
            [],
            {},
            START,
            END,
        )
        row = data["rows"][0]
        self.assertEqual(row["row_id"], "PRJ-009134")
        self.assertEqual(row["project_id"], "PRJ-009134")
        self.assertEqual(row["cells"]["2026-11-10"]["hours"], 2.0)
        self.assertEqual(row["cells"]["2026-11-12"]["hours"], 2.0)
        self.assertEqual(row["total"], 4.0)
        self.assertEqual(data["totals"]["2026-11-10"], 2.0)
        self.assertEqual(data["grand"], 4.0)
        self.assertIn("task: Send test plan (4h)", row["cells"]["2026-11-10"]["sources"])

    def test_personal_task_ref_resolves_to_project_id(self):
        data = timesheet.build(
            [
                item(
                    key="",
                    title="Fix pump",
                    hours=2.0,
                    worked_days=["2026-11-11"],
                    ref="SO-295546",
                )
            ],
            snapshot(meetings=False),
            [],
            {},
            START,
            END,
        )
        row = next(entry for entry in data["rows"] if entry["row_id"] == "PRJ-009999")
        self.assertEqual(row["project_id"], "PRJ-009999")
        self.assertEqual(row["ref"], "SO-295546")
        self.assertFalse(row["missing_id"])
        self.assertEqual(row["cells"]["2026-11-11"]["hours"], 2.0)

    def test_personal_without_ref_goes_to_none_row_last(self):
        data = timesheet.build(
            [
                item(key="", title="Admin", hours=1.0, worked_days=["2026-11-11"]),
                item(key="0207000", title="Send test plan", hours=2.0, worked_days=["2026-11-11"]),
            ],
            snapshot(meetings=False),
            [],
            {},
            START,
            END,
        )
        self.assertEqual([row["row_id"] for row in data["rows"]], ["PRJ-009134", "none"])
        self.assertEqual(data["rows"][-1]["label"], "No project (admin/overhead)")
        self.assertEqual(data["rows"][-1]["cells"]["2026-11-11"]["hours"], 1.0)

    def test_meeting_minutes_added(self):
        data = timesheet.build([], snapshot(), [], {}, START, END)
        row = data["rows"][0]
        self.assertEqual(row["cells"]["2026-11-10"]["hours"], 1.5)
        self.assertIn("meeting: Kickoff call (1.5h)", row["cells"]["2026-11-10"]["sources"])

    def test_activity_only_row_present(self):
        data = timesheet.build(
            [],
            snapshot(meetings=False),
            [
                {
                    "at": "2026-11-11T10:00:00-08:00",
                    "key": "0207000",
                    "task_id": "t-1",
                    "title": "Send test plan",
                    "mark": "x",
                }
            ],
            {},
            START,
            END,
        )
        row = data["rows"][0]
        self.assertEqual(row["total"], 0.0)
        self.assertEqual(row["activity"]["2026-11-11"], ["ticked: Send test plan"])
        self.assertEqual(row["activity"]["2026-11-10"], ["email: Acme kickoff"])

    def test_edit_overrides_auto(self):
        data = timesheet.build(
            [item(key="0207000", title="Send test plan", hours=2.0, worked_days=["2026-11-10"])],
            snapshot(meetings=False),
            [],
            {"PRJ-009134|2026-11-10": 3.0},
            START,
            END,
        )
        cell = data["rows"][0]["cells"]["2026-11-10"]
        self.assertTrue(cell["edited"])
        self.assertEqual(cell["hours"], 3.0)
        self.assertEqual(cell["auto"], 2.0)
        self.assertEqual(data["rows"][0]["total"], 3.0)

    def test_rounding_to_quarter(self):
        data = timesheet.build(
            [
                item(
                    key="0207000",
                    title="Send test plan",
                    hours=1.0,
                    worked_days=["2026-11-10", "2026-11-11", "2026-11-12"],
                )
            ],
            snapshot(meetings=False),
            [],
            {},
            START,
            END,
        )
        self.assertEqual(data["rows"][0]["cells"]["2026-11-10"]["auto"], 0.25)


class SpreadTests(unittest.TestCase):
    def test_sums_exactly(self):
        cells = timesheet.spread(7.5, ["2026-11-09", "2026-11-10", "2026-11-11"])
        self.assertEqual(sum(cells.values()), 7.5)
        self.assertEqual(cells["2026-11-09"], 2.5)
        self.assertEqual(cells["2026-11-11"], 2.5)

    def test_remainder_to_earliest_days(self):
        cells = timesheet.spread(2.0, ["2026-11-09", "2026-11-10", "2026-11-11"])
        self.assertEqual(sum(cells.values()), 2.0)
        self.assertEqual(cells["2026-11-09"], 0.75)
        self.assertEqual(cells["2026-11-10"], 0.75)
        self.assertEqual(cells["2026-11-11"], 0.5)

    def test_no_days(self):
        self.assertEqual(timesheet.spread(4.0, []), {})


class TimesheetPageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.folder = self.vault / "Acme SO-281090 0207000"
        self.folder.mkdir()
        self.note = self.folder / "Acme SO-281090 0207000.md"
        self.note.write_text(NOTE_TEXT, encoding="utf-8")
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
        self.state_path.write_text(
            json.dumps({"tick_log": [], "due_overrides": {}}), encoding="utf-8"
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
                    "name": "Acme SO-281090 0207000",
                    "folder": str(self.folder),
                    "note_path": str(self.note),
                    "customer": "Acme",
                    "project_no": "0207000",
                    "so": ["SO-281090"],
                    "qte": [],
                    "stage": "Pre-Fabrication",
                    "sheet": {
                        "project_id": "PRJ-009134",
                        "customer": "Acme",
                        "so": "281090",
                        "tab": "Active",
                    },
                    "tasks": [
                        {
                            "id": "p0207000-send_test_plan",
                            "title": "Send test plan",
                            "stage": "Pre-Fabrication",
                            "due": "2026-11-12",
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
            "ape_sheet": {"ok": True},
            "ape_sheet_index": {},
        }

    def post(self, url: str, payload: dict):
        headers = dict(HOST)
        headers["X-Hub-Token"] = self.token
        headers["Origin"] = ORIGIN
        return self.client.post(url, json=payload, headers=headers)

    def page(self, start: str = "2026-11-09", end: str = "2026-11-15") -> str:
        response = self.client.get(
            "/timesheet?start=%s&end=%s" % (start, end), headers=HOST
        )
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def test_page_renders_project_id_and_copy(self):
        html = self.page()
        self.assertIn("PRJ-009134", html)
        self.assertIn("Copy", html)
        self.assertIn('data-action="ts-spread"', html)
        self.assertIn("Timesheet", html)

    def test_page_defaults_to_current_week(self):
        response = self.client.get("/timesheet", headers=HOST)
        self.assertEqual(response.status_code, 200)
        response = self.client.get("/timesheet?start=nope&end=2026-11-15", headers=HOST)
        self.assertEqual(response.status_code, 200)

    def test_cell_edit_then_clear(self):
        response = self.post(
            "/api/timesheet/cell",
            {"row_id": "PRJ-009134", "day": "2026-11-10", "hours": 3},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        html = self.page()
        self.assertIn('data-day="2026-11-10" value="3"', html)
        self.assertIn("ts-cell edited", html)
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["timesheet_edits"]["PRJ-009134|2026-11-10"], 3.0)
        cleared = self.post(
            "/api/timesheet/cell", {"row_id": "PRJ-009134", "day": "2026-11-10", "hours": ""}
        )
        self.assertEqual(cleared.status_code, 200)
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertNotIn("PRJ-009134|2026-11-10", state["timesheet_edits"])
        html = self.page()
        self.assertIn('data-day="2026-11-10" value="2"', html)

    def test_cell_bad_input(self):
        for payload in (
            {"row_id": "", "day": "2026-11-10", "hours": 1},
            {"row_id": "PRJ-009134", "day": "2026-13-01", "hours": 1},
            {"row_id": "PRJ-009134", "day": "2026-11-10", "hours": 25},
            {"row_id": "PRJ-009134", "day": "2026-11-10", "hours": -1},
            {"row_id": "PRJ-009134", "day": "2026-11-10", "hours": "abc"},
        ):
            response = self.post("/api/timesheet/cell", payload)
            self.assertEqual(response.status_code, 400, payload)

    def test_spread_over_active_days(self):
        response = self.post(
            "/api/timesheet/spread",
            {"row_id": "PRJ-009134", "start": "2026-11-09", "end": "2026-11-15", "total": 6},
        )
        self.assertEqual(response.status_code, 200)
        cells = response.get_json()["cells"]
        self.assertEqual(sorted(cells), ["2026-11-10", "2026-11-12"])
        self.assertEqual(sum(cells.values()), 6.0)
        html = self.page()
        self.assertIn('data-day="2026-11-10" value="3"', html)
        self.assertIn('data-day="2026-11-12" value="3"', html)

    def test_spread_weekday_fallback(self):
        response = self.post(
            "/api/timesheet/spread",
            {"row_id": "PRJ-000000", "start": "2026-11-09", "end": "2026-11-15", "total": 5},
        )
        self.assertEqual(response.status_code, 200)
        cells = response.get_json()["cells"]
        self.assertEqual(
            sorted(cells),
            ["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"],
        )
        self.assertEqual(sum(cells.values()), 5.0)

    def test_spread_bad_input(self):
        for payload in (
            {"row_id": "", "start": "2026-11-09", "end": "2026-11-15", "total": 4},
            {"row_id": "PRJ-009134", "start": "nope", "end": "2026-11-15", "total": 4},
            {"row_id": "PRJ-009134", "start": "2026-11-15", "end": "2026-11-09", "total": 4},
            {"row_id": "PRJ-009134", "start": "2026-11-09", "end": "2026-11-15", "total": "abc"},
        ):
            response = self.post("/api/timesheet/spread", payload)
            self.assertEqual(response.status_code, 400, payload)


if __name__ == "__main__":
    unittest.main()
