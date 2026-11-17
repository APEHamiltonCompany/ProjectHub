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
from tracker import tracker as tracker_mod
from tracker.hub import __main__ as hub_main

EXAMPLE = ROOT / "tracker" / "config.example.yaml"


def example_base() -> dict:
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


class InitialsTests(unittest.TestCase):
    def test_two_words(self):
        self.assertEqual(setup_wizard.initials_for("Ada Lovelace"), "AL")

    def test_three_words_uses_first_and_last(self):
        self.assertEqual(setup_wizard.initials_for("Ada Byron Lovelace"), "AL")

    def test_single_word(self):
        self.assertEqual(setup_wizard.initials_for("Ada"), "A")

    def test_empty(self):
        self.assertEqual(setup_wizard.initials_for(""), "")


class DefaultVaultTests(unittest.TestCase):
    def test_prefers_desktop_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            desktop = Path(tmp) / "Desktop" / "Projects" / "Project Specific Documents"
            desktop.mkdir(parents=True)
            self.assertEqual(setup_wizard.default_vault(tmp), str(desktop))

    def test_falls_back_to_projects_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp) / "Projects" / "Project Specific Documents"
            projects.mkdir(parents=True)
            self.assertEqual(setup_wizard.default_vault(tmp), str(projects))

    def test_default_when_nothing_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                setup_wizard.default_vault(tmp),
                str(Path(tmp) / "Projects" / "Project Specific Documents"),
            )


class BuildConfigTests(unittest.TestCase):
    def test_writes_answers_with_forward_slashes(self):
        cfg = setup_wizard.build_config(
            example_base(),
            {
                "name": "Ada Lovelace",
                "email": "ada@example.com",
                "initials": "AL",
                "vault": "C:\\Users\\ada\\OneDrive\\Projects",
                "personal_tasks_path": "C:\\Users\\ada\\OneDrive\\Projects\\My Tasks.md",
                "time_estimate_template": "",
                "extra_eml_folder": "C:\\Users\\ada\\OneDrive\\_email_inbox",
                "wire_diagram_builder": "C:\\tools\\WireDiagramBuilder.exe",
                "port": 9100,
            },
        )
        self.assertEqual(cfg["vault"], "C:/Users/ada/OneDrive/Projects")
        self.assertEqual(cfg["personal_tasks_path"], "C:/Users/ada/OneDrive/Projects/My Tasks.md")
        self.assertEqual(cfg["me"], {"name": "Ada Lovelace", "email": "ada@example.com"})
        self.assertEqual(cfg["ape_sheet"]["my_initials"], "AL")
        self.assertEqual(cfg["eml_folders"], ["email_drop", "C:/Users/ada/OneDrive/_email_inbox"])
        self.assertEqual(cfg["tools"], {"wire_diagram_builder": "C:/tools/WireDiagramBuilder.exe"})
        self.assertEqual(cfg["hub"]["port"], 9100)
        self.assertEqual(cfg["ape_sheet"]["url"], example_base()["ape_sheet"]["url"])
        self.assertIn("holidays", cfg)
        self.assertNotIn("state_path", cfg)

    def test_blank_extra_folder_leaves_email_drop_only(self):
        cfg = setup_wizard.build_config(example_base(), {"extra_eml_folder": "", "port": 8765})
        self.assertEqual(cfg["eml_folders"], ["email_drop"])
        self.assertEqual(cfg["tools"], {"wire_diagram_builder": ""})


class RerunTests(unittest.TestCase):
    def test_rerun_uses_existing_config_and_writes_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = str(Path(tmp) / "vault").replace("\\", "/")
            target = Path(tmp) / "config.yaml"
            target.write_text(
                yaml.safe_dump(
                    {
                        "vault": vault,
                        "personal_tasks_path": vault + "/My Tasks.md",
                        "me": {"name": "Old Name", "email": "old@example.com"},
                        "ape_sheet": {"my_initials": "ON"},
                        "hub": {"port": 9000},
                        "tools": {"wire_diagram_builder": ""},
                    }
                ),
                encoding="utf-8",
            )
            code = setup_wizard.main(["--defaults", "--config", str(target)], defaults={})
            self.assertEqual(code, 0)
            self.assertTrue(Path(str(target) + ".bak").is_file())
            cfg = yaml.safe_load(target.read_text(encoding="utf-8"))
            self.assertEqual(cfg["me"]["name"], "Old Name")
            self.assertEqual(cfg["me"]["email"], "old@example.com")
            self.assertEqual(cfg["ape_sheet"]["my_initials"], "ON")
            self.assertEqual(cfg["hub"]["port"], 9000)
            self.assertEqual(cfg["vault"], vault)


class SettingsCleanupTests(unittest.TestCase):
    def test_overrides_that_differ_are_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "config.yaml"
            settings = Path(tmp) / "settings.json"
            settings.write_text(
                json.dumps(
                    {
                        "vault": "C:/old/vault",
                        "personal_tasks_path": "C:/old/vault/My Tasks.md",
                        "time_estimate_template": "C:/old/template.xlsx",
                        "theme": "dark",
                    }
                ),
                encoding="utf-8",
            )
            answers = {
                "vault": "C:/new/vault",
                "personal_tasks_path": "C:/new/vault/My Tasks.md",
                "time_estimate_template": "C:/old/template.xlsx",
            }
            removed = setup_wizard.clean_settings(target, answers)
            self.assertEqual(sorted(removed), ["personal_tasks_path", "vault"])
            data = json.loads(settings.read_text(encoding="utf-8"))
            self.assertEqual(data, {"time_estimate_template": "C:/old/template.xlsx", "theme": "dark"})


class PromptFlowTests(unittest.TestCase):
    def test_prompt_flow_writes_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "config.yaml"
            vault = Path(tmp) / "vault"
            replies = iter(
                [
                    "Ada Lovelace",
                    "ada@example.com",
                    "AL",
                    str(vault),
                    "y",
                    "",
                    "",
                    "",
                    "",
                    "9100",
                    "n",
                ]
            )
            prompts: list[str] = []

            def ask(prompt: str) -> str:
                prompts.append(prompt)
                return next(replies)

            code = setup_wizard.main(["--config", str(target)], ask=ask, defaults={})
            self.assertEqual(code, 0)
            cfg = yaml.safe_load(target.read_text(encoding="utf-8"))
            self.assertEqual(cfg["me"]["name"], "Ada Lovelace")
            self.assertEqual(cfg["me"]["email"], "ada@example.com")
            self.assertEqual(cfg["ape_sheet"]["my_initials"], "AL")
            self.assertEqual(cfg["vault"], str(vault).replace("\\", "/"))
            self.assertEqual(cfg["personal_tasks_path"], str(vault / "My Tasks.md").replace("\\", "/"))
            self.assertEqual(cfg["hub"]["port"], 9100)
            self.assertTrue((vault / "My Tasks.md").is_file())
            self.assertTrue(any("Project folder root" in prompt for prompt in prompts))
            self.assertFalse(Path(str(target) + ".bak").exists())


class MissingConfigTests(unittest.TestCase):
    def test_tracker_main_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.yaml"
            self.assertEqual(tracker_mod.main(["--config", str(missing)]), 2)

    def test_hub_main_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.yaml"
            self.assertEqual(hub_main.main(["--config", str(missing)]), 2)


if __name__ == "__main__":
    unittest.main()
