from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tracker import setup_wizard
from tracker.hub.app import create_app
from tests.test_hub import HOST, ORIGIN

EXAMPLE = ROOT / "tracker" / "config.example.yaml"

NOTE_TEXT = """---
project_no: "0207000"
so: ["SO-1"]
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
- [ ] Parts Required for Testing list \U0001F4C5 2026-11-02 \0001F194 parts_for_testing
<!-- tracker:end -->
"""


class FileTemplateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.folder = self.vault / "Acme SO-1 0207000"
        self.folder.mkdir()
        self.note = self.folder / "Acme SO-1 0207000.md"
        self.note.write_text(NOTE_TEXT, encoding="utf-8")
        self.tpl_dir = self.root / "tpl"
        self.tpl_dir.mkdir()
        self.source = self.tpl_dir / "PartsRequestSO-XXXXXXX.xlsx"
        self.source.write_bytes(b"PK\x03\x04template-bytes")
        self.extra = self.root / "extra"
        self.extra.mkdir()
        self.state_path = self.root / "state.json"
        self.snapshot_path = self.root / "snapshot.json"
        self.config_path = self.root / "config.yaml"
        self.launched = []
        self.app = self.build_app()
        self.client = self.app.test_client()
        self.token = self.app.config["HUB_TOKEN"]

    def tearDown(self):
        self.tmp.cleanup()

    def templates(self) -> dict:
        return {
            "parts_request": {
                "source": str(self.source),
                "name": "PartsRequest {ident}.xlsx",
                "folders": ["{project_folder}"],
                "fill": {},
            },
            "so_request": {
                "source": str(self.source),
                "name": "SO {so}.xlsx",
                "folders": ["{project_folder}"],
                "fill": {},
            },
        }

    def project(self, so=None, qte=None, project_no="0207000") -> dict:
        return {
            "key": "0207000",
            "name": "Acme SO-1 0207000",
            "folder": str(self.folder),
            "note_path": str(self.note),
            "customer": "Acme",
            "project_no": project_no,
            "so": ["SO-1"] if so is None else so,
            "qte": ["QTE-1"] if qte is None else qte,
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
                    "id": "parts_for_testing",
                    "title": "Parts Required for Testing list",
                    "stage": "Pre-Fabrication",
                    "due": "2026-11-02",
                    "status": "open",
                    "mark": " ",
                    "source": "tracker",
                }
            ],
            "emails": [],
        }

    def snapshot(self, project: dict | None = None) -> dict:
        return {
            "generated": "2026-11-10T06:30:00-08:00",
            "today": "2026-11-10",
            "projects": [project or self.project()],
            "pending": [],
            "unmatched": [],
        }

    def build_app(self, launch: str = "template:parts_request", templates: dict | None = None,
                  project: dict | None = None):
        rules_path = self.root / "rules.yaml"
        rules_path.write_text(
            yaml.safe_dump(
                {
                    "stages": ["Pre-Fabrication", "System Test", "Closeout", "Completion"],
                    "workflow": [
                        {
                            "id": "parts_for_testing",
                            "stage": "Pre-Fabrication",
                            "title": "Parts Required for Testing list",
                            "launch": launch,
                        },
                        {"id": "time_estimate", "stage": "Pre-Fabrication", "title": "Time estimate"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        cfg = {
            "vault": str(self.vault),
            "exclude_folders": ["General"],
            "state_path": str(self.state_path),
            "snapshot_path": str(self.snapshot_path),
            "log_path": str(self.root / "last_run.log"),
            "backup_root": str(self.root / "backups"),
            "templates_dir": str(self.root / "templates_out"),
            "rules_path": str(rules_path),
            "me": {"name": "Anthony Zunino"},
            "hub": {"port": 8765, "refresh_minutes": 15, "toast": False},
            "tools": {"wire_diagram_builder": ""},
            "file_templates": self.templates() if templates is None else templates,
        }
        self.config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        self.state_path.write_text(
            json.dumps({"pending": {}, "unmatched": [], "email_overrides": {}, "due_overrides": {}}),
            encoding="utf-8",
        )
        self.snapshot_path.write_text(json.dumps(self.snapshot(project)), encoding="utf-8")
        app = create_app(self.config_path, port=8765, no_refresh=True, runner=lambda: 0)
        app.testing = True
        app.config["LAUNCHER"] = self.launched.append
        return app

    def post(self, url: str, payload: dict, app=None):
        app = app or self.app
        headers = dict(HOST)
        headers["X-Hub-Token"] = app.config["HUB_TOKEN"]
        headers["Origin"] = ORIGIN
        return app.test_client().post(url, json=payload, headers=headers)

    def page(self, app=None) -> str:
        app = app or self.app
        return app.test_client().get("/p/0207000", headers=HOST).get_data(as_text=True)

    def launch(self, app=None):
        return self.post("/api/task/launch", {"key": "0207000", "task_id": "parts_for_testing"}, app)

    def test_creates_copy_with_ident_and_opens_it(self):
        before = self.source.read_bytes()
        mtime = self.source.stat().st_mtime_ns
        response = self.launch()
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        dest = self.folder / "PartsRequest SO-1.xlsx"
        self.assertEqual(payload["launched"], str(dest))
        self.assertEqual(payload["created"], [str(dest)])
        self.assertEqual(payload["existing"], [])
        self.assertEqual(dest.read_bytes(), before)
        self.assertEqual(self.launched, [str(dest)])
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(self.source.stat().st_mtime_ns, mtime)

    def test_second_click_opens_existing_without_overwriting(self):
        dest = self.folder / "PartsRequest SO-1.xlsx"
        self.assertEqual(self.launch().status_code, 200)
        dest.write_bytes(b"edited by hand")
        response = self.launch()
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["launched"], str(dest))
        self.assertEqual(payload["created"], [])
        self.assertEqual(payload["existing"], [str(dest)])
        self.assertEqual(dest.read_bytes(), b"edited by hand")
        self.assertEqual(self.launched, [str(dest), str(dest)])

    def test_qte_only_project_uses_qte(self):
        app = self.build_app(project=self.project(so=[], qte=["QTE-9"]))
        response = self.launch(app)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["launched"], str(self.folder / "PartsRequest QTE-9.xlsx"))

    def test_project_no_used_when_no_so_or_qte(self):
        app = self.build_app(project=self.project(so=[], qte=[]))
        response = self.launch(app)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["launched"], str(self.folder / "PartsRequest 0207000.xlsx"))

    def test_missing_token_is_an_error(self):
        app = self.build_app(launch="template:so_request", project=self.project(so=[], qte=["QTE-9"]))
        response = self.launch(app)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "this project has no so")
        self.assertEqual(self.launched, [])
        self.assertEqual(list(self.folder.glob("*.xlsx")), [])

    def test_extra_folder_gets_its_own_copy(self):
        templates = self.templates()
        templates["parts_request"]["folders"] = ["{project_folder}", str(self.extra)]
        app = self.build_app(templates=templates)
        response = self.launch(app)
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        primary = self.folder / "PartsRequest SO-1.xlsx"
        second = self.extra / "PartsRequest SO-1.xlsx"
        self.assertEqual(payload["launched"], str(primary))
        self.assertEqual(payload["created"], [str(primary), str(second)])
        self.assertTrue(primary.is_file())
        self.assertTrue(second.is_file())

    def test_missing_extra_folder_creates_nothing(self):
        templates = self.templates()
        templates["parts_request"]["folders"] = ["{project_folder}", str(self.root / "missing")]
        app = self.build_app(templates=templates)
        response = self.launch(app)
        self.assertEqual(response.status_code, 400)
        self.assertIn("folder not found", response.get_json()["error"])
        self.assertEqual(list(self.folder.glob("*.xlsx")), [])
        self.assertFalse((self.root / "missing").exists())
        self.assertEqual(self.launched, [])

    def test_unknown_template_has_no_button_and_404(self):
        app = self.build_app(launch="template:nope")
        page = self.page(app)
        self.assertNotIn('data-action="task-launch"', page)
        response = self.launch(app)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"], "no shortcut for this task")
        self.assertEqual(self.launched, [])

    def test_rules_template_renders_template_button(self):
        page = self.page()
        self.assertIn('data-action="task-launch"', page)
        self.assertIn("&#128196;", page)
        self.assertIn("PartsRequest", page)
        self.assertIn("%s \u2192 PartsRequest {ident}.xlsx" % self.source, page)

    def test_launch_set_accepts_template_and_rejects_unknown(self):
        ok = self.post(
            "/api/task/launch_set",
            {"key": "0207000", "task_id": "parts_for_testing", "target": "template:parts_request"},
        )
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.get_json()["launch"], "template:parts_request")
        self.assertEqual(ok.get_json()["source"], "project")
        bad = self.post(
            "/api/task/launch_set",
            {"key": "0207000", "task_id": "parts_for_testing", "target": "template:nope"},
        )
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.get_json()["error"], "unknown template: nope")

    def test_destination_equal_to_source_is_refused(self):
        templates = self.templates()
        templates["parts_request"]["folders"] = [str(self.tpl_dir)]
        templates["parts_request"]["name"] = "PartsRequestSO-XXXXXXX.xlsx"
        app = self.build_app(templates=templates)
        before = self.source.read_bytes()
        response = self.launch(app)
        self.assertEqual(response.status_code, 400)
        self.assertIn("destination is the template", response.get_json()["error"])
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(self.launched, [])


class SetupWizardTemplateTests(unittest.TestCase):
    def test_example_carries_file_templates(self):
        base = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
        cfg = setup_wizard.build_config(base, {"extra_eml_folder": "", "port": 8765})
        self.assertIn("parts_request", cfg["file_templates"])
        self.assertEqual(cfg["file_templates"]["parts_request"]["name"], "PartsRequest {ident}.xlsx")

    def test_rerun_keeps_existing_file_templates(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "config.yaml"
            target.write_text(
                yaml.safe_dump({"file_templates": {"mine": {"source": "C:/tpl.xlsx"}}}),
                encoding="utf-8",
            )
            code = setup_wizard.main(["--defaults", "--config", str(target)], defaults={})
            self.assertEqual(code, 0)
            cfg = yaml.safe_load(target.read_text(encoding="utf-8"))
            self.assertEqual(cfg["file_templates"], {"mine": {"source": "C:/tpl.xlsx"}})


if __name__ == "__main__":
    unittest.main()
