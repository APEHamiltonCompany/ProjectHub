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

import setup_wizard
from tracker.tracker import load_config


class SetupWizardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.vault = self.dir / "vault"
        self.vault.mkdir()
        with open(setup_wizard.EXAMPLE_PATH, "r", encoding="utf-8") as fh:
            self.example = yaml.safe_load(fh)

    def tearDown(self):
        self.tmp.cleanup()

    def form(self, **changes):
        form = setup_wizard.form_from_config(setup_wizard.personalize(self.example))
        form.update(vault=str(self.vault), name="Test User", initials="tu", eml_folders=[])
        form.update(changes)
        return form

    def test_personalize_clears_placeholders(self):
        cfg = setup_wizard.personalize(self.example)
        self.assertEqual(cfg["vault"], "")
        self.assertEqual(cfg["time_estimate_template"], "")
        self.assertEqual(cfg["eml_folders"], [(ROOT / "email_drop").as_posix()])
        self.assertNotIn("YOUR.NAME", cfg["ape_sheet"]["path"])

    def test_build_and_write_round_trip(self):
        cfg, errors, _warnings = setup_wizard.build_config(self.form(), self.example)
        self.assertEqual(errors, [])
        self.assertEqual(cfg["vault"], self.vault.as_posix())
        self.assertEqual(cfg["ape_sheet"]["my_initials"], "TU")
        self.assertNotIn("personal_tasks_path", cfg)
        self.assertIn(date(2026, 11, 26), cfg["holidays"])
        target = self.dir / "config.yaml"
        setup_wizard.write_config(cfg, target)
        loaded = load_config(target)
        self.assertEqual(loaded["vault"], self.vault.as_posix())
        self.assertEqual(loaded["me"]["name"], "Test User")
        self.assertEqual(loaded["outlook_com"]["days_back"], 60)
        self.assertEqual(loaded["holidays"], cfg["holidays"])

    def test_validation_errors(self):
        form = self.form(
            vault=str(self.dir / "missing"), days_back="abc", hub_port="80",
            holidays="2026-13-01", pm_schedule_include="(", time_estimate_template=str(self.dir / "x.xlsx"),
            personal_tasks_path=str(self.dir / "tasks.txt"),
        )
        _cfg, errors, _warnings = setup_wizard.build_config(form, self.example)
        self.assertEqual(len(errors), 7, errors)

    def test_missing_email_folder_is_warning(self):
        form = self.form(eml_folders=[str(self.dir / "nope")])
        _cfg, errors, warnings = setup_wizard.build_config(form, self.example)
        self.assertEqual(errors, [])
        self.assertTrue(any("nope" in item for item in warnings))

    def test_clear_overlay_paths(self):
        config = self.dir / "config.yaml"
        (self.dir / "settings.json").write_text('{"vault": "x", "theme": "dark"}', encoding="utf-8")
        self.assertEqual(setup_wizard.clear_overlay_paths(config), ["vault"])
        self.assertEqual((self.dir / "settings.json").read_text(encoding="utf-8").count("vault"), 0)


if __name__ == "__main__":
    unittest.main()
