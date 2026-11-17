from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tracker import setup_gui
from tracker.tracker import load_config


DETECTED = {"name": "Test User", "email": "t@x.com", "initials": "TU", "vault": "C:\\x\\vault",
            "personal_tasks_path": "C:\\x\\vault\\My Tasks.md", "time_estimate_template": ""}


class SetupGuiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.vault = self.dir / "vault"
        self.vault.mkdir()
        with open(setup_gui.EXAMPLE_PATH, "r", encoding="utf-8") as fh:
            self.example = yaml.safe_load(fh)

    def tearDown(self):
        self.tmp.cleanup()

    def form(self, **changes):
        form = setup_gui.form_from_config(setup_gui.personalize(self.example, DETECTED))
        form.update(vault=str(self.vault), name="Test User", initials="tu", eml_folders=[], personal_tasks_path="")
        form.update(changes)
        return form

    def test_personalize_fills_blanks_from_detected(self):
        cfg = setup_gui.personalize(self.example, DETECTED)
        self.assertEqual(cfg["vault"], "C:/x/vault")
        self.assertEqual(cfg["me"]["name"], "Test User")
        self.assertEqual(cfg["ape_sheet"]["my_initials"], "TU")
        self.assertEqual(cfg["eml_folders"], ["email_drop"])
        self.assertEqual(cfg["project_folder_layout"], "customer/so")
        self.assertIn("file_templates", cfg)

    def test_load_base_keeps_existing_config(self):
        target = self.dir / "config.yaml"
        target.write_text("vault: C:/mine\nme:\n  name: Me\n", encoding="utf-8")
        cfg, existing = setup_gui.load_base(target, detect=lambda: DETECTED)
        self.assertTrue(existing)
        self.assertEqual(cfg["vault"], "C:/mine")
        self.assertIn("tools", cfg)

    def test_build_and_write_round_trip(self):
        cfg, errors, _warnings = setup_gui.build_config(self.form(), self.example)
        self.assertEqual(errors, [])
        self.assertEqual(cfg["vault"], self.vault.as_posix())
        self.assertEqual(cfg["ape_sheet"]["my_initials"], "TU")
        self.assertEqual(cfg["personal_tasks_path"], self.vault.as_posix() + "/My Tasks.md")
        self.assertIn(date(2026, 11, 26), cfg["holidays"])
        target = self.dir / "config.yaml"
        setup_gui.write_config(cfg, target)
        loaded = load_config(target)
        self.assertEqual(loaded["vault"], self.vault.as_posix())
        self.assertEqual(loaded["me"]["name"], "Test User")
        self.assertEqual(loaded["outlook_com"]["days_back"], 60)
        self.assertEqual(loaded["holidays"], cfg["holidays"])

    def test_validation_errors(self):
        form = self.form(
            vault=str(self.dir / "missing"), days_back="abc", hub_port="80",
            holidays="2026-13-01", pm_schedule_include="(", time_estimate_template=str(self.dir / "x.xlsx"),
            personal_tasks_path=str(self.dir / "tasks.txt"), wire_diagram_builder=str(self.dir / "no.exe"),
        )
        _cfg, errors, _warnings = setup_gui.build_config(form, self.example)
        self.assertEqual(len(errors), 8, errors)

    def test_missing_email_folder_is_warning(self):
        form = self.form(eml_folders=[str(self.dir / "nope")])
        _cfg, errors, warnings = setup_gui.build_config(form, self.example)
        self.assertEqual(errors, [])
        self.assertTrue(any("nope" in item for item in warnings))

    def test_tools_and_email_saved(self):
        exe = self.dir / "WireDiagramBuilder.exe"
        exe.write_bytes(b"")
        cfg, errors, _w = setup_gui.build_config(self.form(email="a@b.c", wire_diagram_builder=str(exe)), self.example)
        self.assertEqual(errors, [])
        self.assertEqual(cfg["me"]["email"], "a@b.c")
        self.assertEqual(cfg["tools"]["wire_diagram_builder"], exe.as_posix())

if __name__ == "__main__":
    unittest.main()
