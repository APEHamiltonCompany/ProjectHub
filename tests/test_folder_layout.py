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

from tracker import matcher
from tracker import projects as projects_mod
from tracker.hub.app import create_app
from tests.test_hub import HOST, NOTE_TEXT, ORIGIN, PENDING, TEMPLATE_TEXT

CUSTOMER = matcher.LAYOUT_CUSTOMER


class FolderNameTests(unittest.TestCase):
    def test_flat_unchanged(self):
        self.assertEqual(
            matcher.folder_name_for("Medimmune", ["SO-295546"], [], "0207846"), "Medimmune SO-295546 0207846"
        )

    def test_customer_layout(self):
        name = matcher.folder_name_for
        self.assertEqual(name("Medimmune", ["SO-295546", "SO-296000"], [], "0207846", CUSTOMER), "Medimmune/SO-295546")
        self.assertEqual(name("Amgen", [], ["QTE-056793"], "0207779", CUSTOMER), "Amgen/QTE-056793")
        self.assertEqual(name("Amgen", [], [], "0207779", CUSTOMER), "Amgen/0207779")
        self.assertEqual(name("", ["SO-1"], [], "", CUSTOMER), "Unknown Customer/SO-1")
        self.assertEqual(name("A/B: Co", ["SO-1"], [], "", CUSTOMER), "AB Co/SO-1")

    def test_folder_layout_config(self):
        self.assertEqual(matcher.folder_layout({}), matcher.LAYOUT_FLAT)
        self.assertEqual(matcher.folder_layout({"project_folder_layout": "Customer/SO"}), CUSTOMER)
        self.assertEqual(matcher.folder_layout({"project_folder_layout": "bogus"}), matcher.LAYOUT_FLAT)

    def test_split_folder(self):
        self.assertEqual(matcher.split_folder("Acme/SO-1"), ["Acme", "SO-1"])
        self.assertEqual(matcher.split_folder("Acme\\SO-1"), ["Acme", "SO-1"])
        self.assertEqual(matcher.split_folder("Acme SO-1"), ["Acme SO-1"])
        for bad in ("", "../x", "a/../b", "a/b/c", "/abs", "a/"):
            self.assertEqual(matcher.split_folder(bad), [], bad)


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        for rel in (
            "Acme SO-111111 0207001",
            "Medimmune/SO-295546",
            "Medimmune/QTE-056793",
            "Medimmune/General",
            "Adimab",
            "ADM/New Project Folder",
            "ADM/Linked By Name",
            "_Archive/Old/SO-999999",
            "General/SO-123123",
        ):
            (self.vault / rel).mkdir(parents=True)
        (self.vault / "ADM" / "Linked By Name" / "Linked By Name.md").write_text("x", encoding="utf-8")
        (self.vault / "Medimmune" / "SO-295546" / "SO-295546.md").write_text(
            "---\nproject_no: \"0207846\"\n---\n", encoding="utf-8"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def names(self, layout):
        return {
            matcher.relative_folder(self.vault, p.folder): (p.customer, p.key)
            for p in matcher.build_projects(self.vault, ["General"], layout)
        }

    def test_customer_layout(self):
        found = self.names(CUSTOMER)
        self.assertEqual(
            found,
            {
                "Acme SO-111111 0207001": ("Acme", "0207001"),
                "ADM/Linked By Name": ("ADM", "Linked By Name"),
                "Medimmune/QTE-056793": ("Medimmune", "QTE-056793"),
                "Medimmune/SO-295546": ("Medimmune", "0207846"),
            },
        )

    def test_flat_layout_still_detects_customer_folders(self):
        found = self.names(matcher.LAYOUT_FLAT)
        self.assertIn("Medimmune/SO-295546", found)
        self.assertIn("Acme SO-111111 0207001", found)
        self.assertIn("Adimab", found)
        self.assertIn("ADM", found)
        self.assertNotIn("Medimmune", found)


class CreateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.vault = root / "vault"
        self.vault.mkdir()
        templates = root / "templates"
        templates.mkdir()
        (templates / "Project Template.md").write_text(TEMPLATE_TEXT, encoding="utf-8")
        self.cfg = {
            "vault": str(self.vault),
            "templates_dir": str(templates),
            "project_folder_layout": CUSTOMER,
        }

    def tearDown(self):
        self.tmp.cleanup()

    def pending(self, customer="Medimmune"):
        entry = json.loads(json.dumps(PENDING))
        entry["parsed"].update(customer=customer, so=["SO-295546"], qte=[], project_no="0207846")
        entry["suggested_folder"] = "stale flat name"
        return entry

    def test_creates_nested_folder(self):
        info = projects_mod.create_from_pending(self.cfg, self.pending())
        folder = self.vault / "Medimmune" / "SO-295546"
        self.assertEqual(Path(info["folder"]), folder)
        self.assertTrue((folder / "SO-295546.md").is_file())
        project = matcher.build_projects(self.vault, layout=CUSTOMER)[0]
        self.assertEqual((project.customer, project.key), ("Medimmune", "0207846"))

    def test_reuses_existing_customer_folder_case_insensitively(self):
        (self.vault / "MedImmune").mkdir()
        info = projects_mod.create_from_pending(self.cfg, self.pending("Medimmune"))
        self.assertEqual(Path(info["folder"]), self.vault / "MedImmune" / "SO-295546")
        self.assertEqual([p.name for p in self.vault.iterdir()], ["MedImmune"])

    def test_explicit_folder_and_validation(self):
        info = projects_mod.create_from_pending(self.cfg, self.pending(), "Other Co/SO-295546")
        self.assertEqual(Path(info["folder"]), self.vault / "Other Co" / "SO-295546")
        for bad in ("../x", "a/b/c", "a/.."):
            with self.assertRaises(ValueError, msg=bad):
                projects_mod.create_from_pending(self.cfg, self.pending(), bad)
        with self.assertRaises(ValueError):
            projects_mod.create_from_pending(self.cfg, self.pending(), "Other Co/SO-295546")

    def test_dry_run_creates_nothing(self):
        info = projects_mod.create_from_pending(self.cfg, self.pending(), dry_run=True)
        self.assertEqual(Path(info["folder"]), self.vault / "Medimmune" / "SO-295546")
        self.assertEqual(list(self.vault.iterdir()), [])


class HubNestedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        self.folder = self.vault / "Acme" / "SO-111111"
        self.folder.mkdir(parents=True)
        self.note = self.folder / "SO-111111.md"
        self.note.write_text(NOTE_TEXT, encoding="utf-8")
        templates = self.root / "templates"
        templates.mkdir()
        (templates / "Project Template.md").write_text(TEMPLATE_TEXT, encoding="utf-8")
        self.state_path = self.root / "state.json"
        self.backup_root = self.root / "backups"
        cfg = {
            "vault": str(self.vault),
            "project_folder_layout": CUSTOMER,
            "state_path": str(self.state_path),
            "snapshot_path": str(self.root / "snapshot.json"),
            "log_path": str(self.root / "last_run.log"),
            "backup_root": str(self.backup_root),
            "templates_dir": str(templates),
            "rules_path": str(ROOT / "tracker" / "rules.yaml"),
            "hub": {"port": 8765, "refresh_minutes": 15, "toast": False},
        }
        config_path = self.root / "config.yaml"
        config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        self.state_path.write_text(json.dumps({"pending": {"msg-1": dict(PENDING)}}), encoding="utf-8")
        snapshot = {
            "projects": [
                {"key": "0207000", "name": "SO-111111", "folder": str(self.folder), "note_path": str(self.note),
                 "customer": "Acme", "project_no": "0207000", "so": ["SO-111111"], "qte": [], "tasks": [],
                 "emails": [], "links": [], "stage": "Pre-Kickoff"}
            ]
        }
        (self.root / "snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")
        app = create_app(config_path, port=8765, no_refresh=True, runner=lambda: 0)
        app.testing = True
        self.client = app.test_client()
        self.token = app.config["HUB_TOKEN"]

    def tearDown(self):
        self.tmp.cleanup()

    def post(self, url, payload):
        headers = dict(HOST, **{"X-Hub-Token": self.token, "Origin": ORIGIN})
        return self.client.post(url, json=payload, headers=headers)

    def test_archive_and_restore_keep_customer_level(self):
        response = self.post("/api/projects/archive", {"key": "0207000"})
        self.assertEqual(response.status_code, 200, response.get_json())
        target = self.vault / matcher.ARCHIVE_DIR / "Acme" / "SO-111111"
        self.assertTrue(target.is_dir())
        self.assertFalse((self.vault / "Acme").exists(), "empty customer folder should be removed")
        page = self.client.get("/projects", headers=HOST).get_data(as_text=True)
        self.assertIn('data-name="Acme/SO-111111"', page)
        self.assertIn('data-layout="customer/so"', page)
        restored = self.post("/api/projects/restore", {"name": "Acme/SO-111111"})
        self.assertEqual(restored.status_code, 200, restored.get_json())
        self.assertTrue(self.folder.is_dir())
        self.assertFalse((self.vault / matcher.ARCHIVE_DIR / "Acme").exists())
        self.assertEqual(self.post("/api/projects/restore", {"name": "../x"}).status_code, 400)
        self.assertEqual(self.post("/api/projects/restore", {"name": "a/b/c"}).status_code, 400)

    def test_delete_and_restore_deleted_returns_to_customer_folder(self):
        (self.vault / "Acme" / "SO-222222").mkdir()
        response = self.post("/api/projects/delete", {"key": "0207000", "confirm": "0207000"})
        self.assertEqual(response.status_code, 200, response.get_json())
        moved = Path(response.get_json()["moved_to"])
        self.assertRegex(moved.name, r"^\d{8}_\d{6}_Acme - SO-111111$")
        self.assertTrue((self.vault / "Acme").is_dir(), "customer folder with other projects stays")
        restored = self.post("/api/projects/restore_deleted", {"name": moved.name})
        self.assertEqual(restored.status_code, 200, restored.get_json())
        self.assertTrue(self.folder.is_dir())
        self.assertFalse((self.folder / ".projecthub_origin").exists())

    def test_pending_suggestion_uses_layout(self):
        page = self.client.get("/", headers=HOST).get_data(as_text=True)
        parsed = PENDING["parsed"]
        expected = matcher.folder_name_for(
            parsed["customer"], parsed["so"], parsed["qte"], parsed["project_no"], CUSTOMER
        )
        self.assertIn('value="%s"' % expected, page)

    def test_manual_create_nested(self):
        response = self.post(
            "/api/projects/create",
            {"customer": "Beta", "so": "SO-333333", "project_no": "0207333", "level": "L1"},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue((self.vault / "Beta" / "SO-333333" / "SO-333333.md").is_file())


if __name__ == "__main__":
    unittest.main()
