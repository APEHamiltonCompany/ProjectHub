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
from tracker.hub.app import create_app
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
- [ ] Review CPR/SO \U0001F4C5 2026-11-02 \0001F194 P0207000-REVIEW_CPR_SO
- [ ] Time estimate \U0001F4C5 2026-11-02 \U0001F194 time_estimate
<!-- tracker:end -->
"""


class TaskLaunchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.folder = self.vault / "Acme SO-1 0207000"
        self.folder.mkdir()
        self.note = self.folder / "Acme SO-1 0207000.md"
        self.note.write_text(NOTE_TEXT, encoding="utf-8")
        self.exe = self.root / "WireDiagramBuilder.exe"
        self.exe.write_bytes(b"MZ")
        self.other = self.root / "Other Tool.exe"
        self.other.write_bytes(b"MZ")
        self.missing = self.root / "Missing.exe"
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
                            "launch": str(self.exe),
                        },
                        {
                            "id": "time_estimate",
                            "stage": "Pre-Fabrication",
                            "title": "Time estimate",
                            "launch": str(self.missing),
                        },
                        {"id": "wiring_diagram", "stage": "Pre-Fabrication", "title": "Wiring diagram"},
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
        self.launched = []
        self.app = create_app(self.config_path, port=8765, no_refresh=True, runner=lambda: 0)
        self.app.testing = True
        self.app.config["LAUNCHER"] = self.launched.append
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
                        },
                        {
                            "id": "time_estimate",
                            "title": "Time estimate",
                            "stage": "Pre-Fabrication",
                            "due": "2026-11-02",
                            "status": "open",
                            "mark": " ",
                            "source": "tracker",
                        },
                    ],
                    "emails": [],
                }
            ],
            "pending": [],
            "unmatched": [],
        }

    def post(self, url: str, payload: dict, token: str | None = None):
        headers = dict(HOST)
        headers["X-Hub-Token"] = self.token if token is None else token
        headers["Origin"] = ORIGIN
        return self.client.post(url, json=payload, headers=headers)

    def page(self) -> str:
        return self.client.get("/p/0207000", headers=HOST).get_data(as_text=True)

    def frontmatter(self) -> tuple[dict, str]:
        text, _nl, _bom, _enc = notes.read_text(self.note)
        data, _raw, body = notes.split_frontmatter(text)
        return data, body

    def test_template_default_renders_launch_button(self):
        page = self.page()
        self.assertIn('data-action="task-launch"', page)
        self.assertIn('data-task-id="p0207000-review_cpr_so"', page)
        self.assertIn("WireDiagramBuilder", page)
        self.assertIn(str(self.exe), page)

    def test_launch_uses_template_target(self):
        response = self.post("/api/task/launch", {"key": "0207000", "task_id": "p0207000-review_cpr_so"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["launched"], str(self.exe))
        self.assertEqual(self.launched, [str(self.exe)])

    def test_launch_unknown_task_404(self):
        response = self.post("/api/task/launch", {"key": "0207000", "task_id": "nope"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"], "no shortcut for this task")
        self.assertEqual(self.launched, [])

    def test_launch_missing_file_404(self):
        response = self.post("/api/task/launch", {"key": "0207000", "task_id": "time_estimate"})
        self.assertEqual(response.status_code, 404)
        self.assertIn("not found", response.get_json()["error"])
        self.assertEqual(self.launched, [])

    def test_launch_set_writes_frontmatter_and_keeps_body(self):
        before, body_before = self.frontmatter()
        response = self.post(
            "/api/task/launch_set",
            {"key": "0207000", "task_id": "p0207000-review_cpr_so", "target": str(self.other)},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["launch"], str(self.other))
        self.assertEqual(payload["source"], "project")
        after, body_after = self.frontmatter()
        self.assertEqual(body_after, body_before)
        self.assertEqual(after["task_launch"], {"p0207000-review_cpr_so": str(self.other)})
        for key in ("project_no", "customer", "team", "ship_date"):
            self.assertEqual(after[key], before[key])
        launched = self.post("/api/task/launch", {"key": "0207000", "task_id": "p0207000-review_cpr_so"})
        self.assertEqual(launched.status_code, 200)
        self.assertEqual(self.launched, [str(self.other)])

    def test_launch_none_suppresses_and_reset_restores(self):
        suppressed = self.post(
            "/api/task/launch_set", {"key": "0207000", "task_id": "p0207000-review_cpr_so", "target": ""}
        )
        self.assertEqual(suppressed.status_code, 200)
        self.assertEqual(suppressed.get_json()["launch"], "")
        self.assertEqual(suppressed.get_json()["source"], "project")
        self.assertNotIn(
            'data-action="task-launch" data-key="0207000" data-task-id="p0207000-review_cpr_so"', self.page()
        )
        self.assertEqual(
            self.post("/api/task/launch", {"key": "0207000", "task_id": "p0207000-review_cpr_so"}).status_code, 404
        )
        reset = self.post("/api/task/launch_set", {"key": "0207000", "task_id": "p0207000-review_cpr_so", "reset": True})
        self.assertEqual(reset.status_code, 200)
        self.assertEqual(reset.get_json()["launch"], str(self.exe))
        self.assertEqual(reset.get_json()["source"], "template")
        data, _body = self.frontmatter()
        self.assertNotIn("task_launch", data)
        self.assertIn(
            'data-action="task-launch" data-key="0207000" data-task-id="p0207000-review_cpr_so"', self.page()
        )

    def test_launch_set_rejects_missing_path_and_strips_quotes(self):
        bad = self.post(
            "/api/task/launch_set",
            {"key": "0207000", "task_id": "p0207000-review_cpr_so", "target": str(self.root / "nope.exe")},
        )
        self.assertEqual(bad.status_code, 400)
        self.assertIn("existing file/folder", bad.get_json()["error"])
        quoted = self.post(
            "/api/task/launch_set",
            {"key": "0207000", "task_id": "p0207000-review_cpr_so", "target": '"%s"' % self.other},
        )
        self.assertEqual(quoted.status_code, 200)
        self.assertEqual(quoted.get_json()["launch"], str(self.other))
        data, _body = self.frontmatter()
        self.assertEqual(data["task_launch"]["p0207000-review_cpr_so"], str(self.other))

    def test_launch_set_requires_known_project(self):
        self.assertEqual(
            self.post("/api/task/launch_set", {"key": "nope", "task_id": "p0207000-review_cpr_so", "target": ""}).status_code,
            404,
        )
        self.assertEqual(
            self.post("/api/task/launch_set", {"key": "0207000", "task_id": "", "target": ""}).status_code, 400
        )

    def tool_app(self, tools: dict):
        rules_path = self.root / "rules_tool.yaml"
        rules_path.write_text(
            yaml.safe_dump(
                {
                    "stages": ["Pre-Fabrication", "System Test", "Closeout", "Completion"],
                    "workflow": [
                        {
                            "id": "p0207000-review_cpr_so",
                            "stage": "Pre-Fabrication",
                            "title": "Review CPR/SO",
                            "launch": "tool:wire_diagram_builder",
                        },
                        {"id": "time_estimate", "stage": "Pre-Fabrication", "title": "Time estimate"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        cfg = dict(self.cfg)
        cfg["rules_path"] = str(rules_path)
        cfg["tools"] = tools
        self.config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        app = create_app(self.config_path, port=8765, no_refresh=True, runner=lambda: 0)
        app.testing = True
        app.config["LAUNCHER"] = self.launched.append
        return app

    def tool_post(self, app, url: str, payload: dict):
        headers = dict(HOST)
        headers["X-Hub-Token"] = app.config["HUB_TOKEN"]
        headers["Origin"] = ORIGIN
        return app.test_client().post(url, json=payload, headers=headers)

    def test_tool_launch_resolves_from_config(self):
        app = self.tool_app({"wire_diagram_builder": str(self.exe)})
        page = app.test_client().get("/p/0207000", headers=HOST).get_data(as_text=True)
        self.assertIn("WireDiagramBuilder", page)
        self.assertIn(str(self.exe), page)
        response = self.tool_post(app, "/api/task/launch", {"key": "0207000", "task_id": "p0207000-review_cpr_so"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["launched"], str(self.exe))
        self.assertEqual(self.launched, [str(self.exe)])

    def test_unset_tool_has_no_launch_button(self):
        app = self.tool_app({"wire_diagram_builder": ""})
        page = app.test_client().get("/p/0207000", headers=HOST).get_data(as_text=True)
        self.assertNotIn(
            'data-action="task-launch" data-key="0207000" data-task-id="p0207000-review_cpr_so"', page
        )
        response = self.tool_post(app, "/api/task/launch", {"key": "0207000", "task_id": "p0207000-review_cpr_so"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.launched, [])

    def test_apply_update_keeps_task_launch(self):
        path = self.root / "keep.md"
        path.write_text(
            NOTE_TEXT.replace(
                'team: ["A B"]\n',
                'team: ["A B"]\ntask_launch:\n  review_cpr_so: "C:\\\\tools\\\\app.exe"\n',
            ),
            encoding="utf-8",
        )
        changed, _diff = notes.apply_update(path, {}, [], date(2026, 11, 13))
        self.assertTrue(changed)
        data, _raw, _body = notes.split_frontmatter(notes.read_text(path)[0])
        self.assertEqual(data["task_launch"], {"review_cpr_so": "C:\\tools\\app.exe"})


class LauncherFocusTests(unittest.TestCase):
    def test_exe_launch_raises_window_of_new_process(self):
        from unittest import mock

        from tracker.hub import app as app_mod

        proc = mock.Mock(pid=4321)
        with mock.patch.object(app_mod.subprocess, "Popen", return_value=proc) as popen, \
                mock.patch.object(app_mod.winfocus, "top_windows", return_value={1: 2}), \
                mock.patch.object(app_mod.winfocus, "raise_after_launch") as raise_after:
            app_mod.launcher("C:\\tools\\app.exe")
        popen.assert_called_once()
        raise_after.assert_called_once_with({1: 2}, 4321)

    def test_file_launch_raises_any_new_window(self):
        from unittest import mock

        from tracker.hub import app as app_mod

        with mock.patch.object(app_mod.os, "startfile", create=True) as startfile, \
                mock.patch.object(app_mod.winfocus, "top_windows", return_value={}), \
                mock.patch.object(app_mod.winfocus, "raise_after_launch") as raise_after:
            app_mod.launcher("C:\\docs\\plan.xlsx")
        startfile.assert_called_once_with("C:\\docs\\plan.xlsx")
        raise_after.assert_called_once_with({})

    def test_find_launched_window_prefers_process_tree(self):
        from unittest import mock

        from tracker.hub import winfocus

        with mock.patch.object(winfocus, "top_windows", return_value={10: 1, 20: 99, 30: 7}), \
                mock.patch.object(winfocus, "process_tree", return_value={5, 7}):
            self.assertEqual(winfocus.find_launched_window({10: 1}, 5, timeout=1), 30)


if __name__ == "__main__":
    unittest.main()
