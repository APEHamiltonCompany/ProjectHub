from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tracker import settings as settings_mod
from tracker.hub.app import create_app
from tracker.tracker import load_config

HOST = {"Host": "127.0.0.1:8765"}
ORIGIN = "http://127.0.0.1:8765"
NOTE_TEXT = """---
project_no: "0207000"
customer: "Acme"
stage: "Pre-Fabrication"
---

**TO DO:**

- [ ] Review CPR/SO \U0001F4C5 2026-11-02 \U0001F194 p0207000-review_cpr_so
"""


class OverlayTests(unittest.TestCase):
    def test_apply_overlay_top_and_hub_keys(self):
        cfg = {"vault": "C:/old", "hub": {"port": 8765, "refresh_minutes": 15}}
        overlay = {
            "vault": "C:/new",
            "personal_tasks_path": "C:/new/My Tasks.md",
            "time_estimate_template": "C:/new/TimeEstimates.xlsx",
            "refresh_minutes": 20,
            "due_soon_days": 3,
            "theme": "dark",
            "auto_reload_pages": False,
        }
        merged = settings_mod.apply_overlay(cfg, overlay)
        self.assertEqual(merged["vault"], "C:/new")
        self.assertEqual(merged["personal_tasks_path"], "C:/new/My Tasks.md")
        self.assertEqual(merged["time_estimate_template"], "C:/new/TimeEstimates.xlsx")
        self.assertEqual(merged["hub"]["refresh_minutes"], 20)
        self.assertEqual(merged["hub"]["due_soon_days"], 3)
        self.assertEqual(merged["hub"]["theme"], "dark")
        self.assertFalse(merged["hub"]["auto_reload_pages"])
        self.assertEqual(merged["hub"]["port"], 8765)
        self.assertEqual(cfg["vault"], "C:/old")
        self.assertEqual(cfg["hub"], {"port": 8765, "refresh_minutes": 15})

    def test_apply_overlay_ignores_unknown_and_bad_values(self):
        cfg = {"vault": "C:/old", "hub": {}}
        merged = settings_mod.apply_overlay(
            cfg, {"vault": "  ", "theme": "purple", "refresh_minutes": -1, "due_soon_days": "7", "nope": 1}
        )
        self.assertEqual(merged["vault"], "C:/old")
        self.assertEqual(merged["hub"], {})

    def test_load_config_merges_settings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.yaml"
            config_path.write_text(yaml.safe_dump({"vault": "C:/old", "hub": {"refresh_minutes": 15}}), encoding="utf-8")
            settings_mod.save_overlay(
                settings_mod.settings_path_for(config_path), {"vault": "C:/new", "refresh_minutes": 30}
            )
            cfg = load_config(config_path)
            self.assertEqual(cfg["vault"], "C:/new")
            self.assertEqual(cfg["hub"]["refresh_minutes"], 30)
            self.assertTrue((root / "settings.json").is_file())


class SettingsHubTests(unittest.TestCase):
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
        self.state_path.write_text(json.dumps({"ui": {}}), encoding="utf-8")
        self.snapshot_path.write_text(
            json.dumps(
                {
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
        self.runs = []
        self.app = create_app(
            self.config_path, port=8765, no_refresh=True, runner=lambda: self.runs.append(1) or 0
        )
        self.app.testing = True
        self.client = self.app.test_client()
        self.token = self.app.config["HUB_TOKEN"]

    def tearDown(self):
        self.tmp.cleanup()

    def post(self, url: str, payload: dict, token: str | None = None):
        headers = dict(HOST)
        headers["X-Hub-Token"] = self.token if token is None else token
        headers["Origin"] = ORIGIN
        return self.client.post(url, json=payload, headers=headers)

    def ui_state(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8"))["ui"]

    def test_ui_merge_and_validation(self):
        self.assertEqual(self.post("/api/ui", {"layout": "timeline"}).status_code, 200)
        response = self.post("/api/ui", {"zoom": 80})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["ui"]["zoom"], 80)
        self.assertEqual(response.get_json()["ui"]["layout"], "timeline")
        response = self.post("/api/ui", {"fit": False})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["ui"]["fit"])
        state = self.ui_state()
        self.assertEqual(state["layout"], "timeline")
        self.assertEqual(state["zoom"], 80)
        self.assertFalse(state["fit"])
        self.assertEqual(self.post("/api/ui", {"zoom": 65}).status_code, 400)
        self.assertEqual(self.post("/api/ui", {"zoom": "wide"}).status_code, 400)
        self.assertEqual(self.post("/api/ui", {"fit": "yes"}).status_code, 400)
        self.assertEqual(self.post("/api/ui", {"layout": "bogus"}).status_code, 400)
        self.assertEqual(self.post("/api/ui", {}).status_code, 400)

    def test_settings_saved_and_rendered(self):
        response = self.post("/api/settings", {"refresh_minutes": 20, "theme": "dark"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["settings"]["refresh_minutes"], 20)
        self.assertFalse(response.get_json()["rerun"])
        overlay = json.loads((self.root / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(overlay["refresh_minutes"], 20)
        self.assertEqual(overlay["theme"], "dark")
        page = self.client.get("/settings", headers=HOST).get_data(as_text=True)
        self.assertIn('data-theme="dark"', page)
        self.assertIn('value="20"', page)
        self.assertEqual(self.post("/api/settings", {"theme": "purple"}).status_code, 400)
        self.assertEqual(self.post("/api/settings", {"refresh_minutes": 5000}).status_code, 400)

    def test_settings_vault_change_reruns(self):
        self.assertEqual(self.post("/api/settings", {"vault": str(self.root / "missing")}).status_code, 400)
        new_vault = self.root / "vault2"
        new_vault.mkdir()
        response = self.post("/api/settings", {"vault": str(new_vault)})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["rerun"])
        deadline = time.monotonic() + 5
        while not self.runs and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(self.runs)

    def test_settings_ape_sheet_path(self):
        folder = self.root / "sheet_folder"
        folder.mkdir()
        expected = str(folder).replace("\\", "/")
        response = self.post("/api/settings", {"ape_sheet_path": str(folder)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["settings"]["ape_sheet_path"], expected)
        overlay = json.loads((self.root / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(overlay["ape_sheet_path"], expected)
        self.assertEqual(
            self.post("/api/settings", {"ape_sheet_path": str(self.root / "nope")}).status_code, 400
        )
        self.assertEqual(self.post("/api/settings", {"ape_sheet_path": ""}).status_code, 200)
        overlay = json.loads((self.root / "settings.json").read_text(encoding="utf-8"))
        self.assertNotIn("ape_sheet_path", overlay)

    def test_settings_requires_token(self):
        self.assertEqual(self.post("/api/settings", {"theme": "dark"}, token="wrong").status_code, 403)

    def test_layout_switch_disabled_outside_dashboard(self):
        project = self.client.get("/p/0207000", headers=HOST).get_data(as_text=True)
        self.assertIn('aria-disabled="true"', project)
        self.assertIn("layout-switch is-disabled", project)
        dashboard = self.client.get("/", headers=HOST).get_data(as_text=True)
        self.assertNotIn("layout-switch is-disabled", dashboard)
        self.assertIn('id="fit-toggle"', dashboard)

    def test_status_includes_generated(self):
        data = self.client.get("/api/status", headers=HOST).get_json()
        self.assertEqual(data["generated"], "2026-11-10T06:30:00-08:00")
        self.assertIn("running", data)


if __name__ == "__main__":
    unittest.main()
