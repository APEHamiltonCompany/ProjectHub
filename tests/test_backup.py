from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tracker import backup  # noqa: E402
from tracker import settings as settings_mod  # noqa: E402
from tracker import setup_gui, setup_wizard  # noqa: E402
from tracker.hub.app import create_app  # noqa: E402
from tests.test_hub import HOST, ORIGIN  # noqa: E402

RULES = ROOT / "tracker" / "rules.yaml"
OLD = time.time() - 10 * 86400


def write(path: Path, text: str, mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def bump(path: Path, text: str, seconds: float = 100) -> None:
    before = path.stat().st_mtime
    path.write_text(text, encoding="utf-8")
    os.utime(path, (before + seconds, before + seconds))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.src = self.root / "R" / "My Projects"
        self.dst = self.root / "E" / "My Projects"
        self.data = self.root / "data"
        self.store = self.root / "backups"
        self.src.mkdir(parents=True)
        self.pair = {"name": "vault", "source": str(self.src), "backup": str(self.dst)}
        self.cfg = {"vault": str(self.src), "backup_vault": {"path": str(self.dst), "daily_time": "14:00"}}

    def tearDown(self):
        self.tmp.cleanup()

    def export(self, today=None):
        return backup.export_pair(self.pair, self.data, today=today)

    def preview(self):
        return {item["rel"]: item for item in backup.preview_import(self.cfg, self.data)["items"]}

    def apply(self, rels, today=date(2026, 11, 18)):
        return backup.apply_import(self.cfg, [{"pair": "vault", "rel": rel} for rel in rels], self.store, self.data, today)


class ExportTests(Base):
    def test_first_export_copies_everything(self):
        write(self.src / "A" / "SO-1" / "note.md", "one", OLD)
        write(self.src / "big.bin", "x" * 5000, OLD)
        write(self.src / "A" / "~$lock.xlsx", "lock", OLD)
        stats = self.export()
        self.assertEqual(stats["copied"], 2)
        self.assertEqual((self.dst / "A" / "SO-1" / "note.md").read_text(encoding="utf-8"), "one")
        self.assertFalse((self.dst / "A" / "~$lock.xlsx").exists())
        self.assertEqual(self.export()["copied"], 0)

    def test_changed_on_r_is_copied(self):
        note = write(self.src / "n.md", "v1", OLD)
        self.export()
        bump(note, "v2")
        self.assertEqual(self.export()["copied"], 1)
        self.assertEqual((self.dst / "n.md").read_text(encoding="utf-8"), "v2")

    def test_deleted_on_r_moves_to_deleted_folder(self):
        note = write(self.src / "A" / "n.md", "v1", OLD)
        write(self.src / "keep.md", "k", OLD)
        self.export()
        note.unlink()
        stats = self.export(today=date(2026, 11, 18))
        self.assertEqual(stats["deleted"], 1)
        self.assertFalse((self.dst / "A" / "n.md").exists())
        self.assertTrue((self.dst / backup.DELETED_DIR / "2026-11-18" / "A" / "n.md").is_file())
        self.assertNotIn(backup.DELETED_DIR, " ".join(backup.scan(self.dst)))

    def test_mass_delete_is_held(self):
        for index in range(60):
            write(self.src / ("f%02d.md" % index), "x", OLD)
        self.export()
        for index in range(60):
            (self.src / ("f%02d.md" % index)).unlink()
        write(self.src / "only.md", "x", OLD)
        stats = self.export()
        self.assertEqual(stats["deleted"], 0)
        self.assertEqual(stats["deletes_held"], 60)
        self.assertTrue((self.dst / "f00.md").is_file())

    def test_changed_in_backup_waits_for_import(self):
        write(self.src / "n.md", "v1", OLD)
        self.export()
        bump(self.dst / "n.md", "offline edit")
        stats = self.export()
        self.assertEqual(stats["pending"], 1)
        self.assertEqual((self.dst / "n.md").read_text(encoding="utf-8"), "offline edit")

    def test_new_in_backup_is_left_alone(self):
        write(self.src / "n.md", "v1", OLD)
        self.export()
        write(self.dst / "made offline.md", "new")
        stats = self.export()
        self.assertEqual(stats["only_backup"], 1)
        self.assertTrue((self.dst / "made offline.md").is_file())
        self.assertFalse((self.src / "made offline.md").exists())

    def test_first_run_with_existing_copy_never_overwrites_newer(self):
        write(self.src / "same.md", "s", OLD)
        write(self.dst / "same.md", "s", OLD)
        write(self.src / "older_on_e.md", "r new", OLD + 500)
        write(self.dst / "older_on_e.md", "e old", OLD)
        write(self.src / "newer_on_e.md", "r old", OLD)
        write(self.dst / "newer_on_e.md", "e new", OLD + 500)
        write(self.dst / "only_e.md", "e", OLD)
        stats = self.export()
        self.assertEqual(stats["copied"], 1)
        self.assertEqual(stats["pending"], 1)
        self.assertEqual(stats["only_backup"], 1)
        self.assertEqual((self.dst / "older_on_e.md").read_text(encoding="utf-8"), "r new")
        self.assertEqual((self.dst / "newer_on_e.md").read_text(encoding="utf-8"), "e new")
        self.assertTrue((self.dst / "only_e.md").is_file())
        items = self.preview()
        self.assertEqual(items["newer_on_e.md"]["kind"], "newer")
        self.assertFalse(items["newer_on_e.md"]["checked"])
        self.assertEqual((items["only_e.md"]["kind"], items["only_e.md"]["checked"]), ("old", False))
        self.assertNotIn("same.md", items)
        self.assertNotIn("older_on_e.md", items)
        self.export()
        write(self.dst / "made offline.md", "n")
        bump(self.dst / "only_e.md", "edited offline")
        items = self.preview()
        self.assertEqual(items["made offline.md"]["kind"], "new")
        self.assertEqual(items["only_e.md"]["kind"], "new")
        self.apply(["only_e.md"])
        self.assertEqual((self.src / "only_e.md").read_text(encoding="utf-8"), "edited offline")
        self.assertNotIn("only_e.md", self.preview())

    def test_open_file_is_skipped_and_retried(self):
        write(self.src / "a.md", "a", OLD)
        write(self.src / "open.xlsx", "o", OLD)
        real = backup.copy_file

        def flaky(source, target):
            if Path(source).name == "open.xlsx":
                raise PermissionError(13, "in use")
            real(source, target)

        with mock.patch.object(backup, "copy_file", flaky):
            stats = self.export()
        self.assertEqual(stats["copied"], 1)
        self.assertEqual(len(stats["skipped"]), 1)
        self.assertIn("open.xlsx", stats["skipped"][0])
        self.assertEqual(self.export()["copied"], 1)
        self.assertTrue((self.dst / "open.xlsx").is_file())

    def test_unreachable_or_empty_source_raises(self):
        write(self.src / "a.md", "a", OLD)
        self.export()
        (self.src / "a.md").unlink()
        with self.assertRaises(OSError):
            self.export()
        self.assertTrue((self.dst / "a.md").is_file())
        bad = dict(self.pair, source=str(self.root / "missing"))
        with self.assertRaises(OSError):
            backup.export_pair(bad, self.data)

    def test_export_all_covers_extra_folders(self):
        templates = self.root / "R" / "TEMPLATES"
        write(templates / "t.xlsx", "t", OLD)
        write(self.src / "a.md", "a", OLD)
        target = self.root / "E" / "TEMPLATES"
        self.cfg["backup_vault"]["folders"] = [{"source": str(templates), "backup": str(target)}]
        result = backup.export_all(self.cfg, self.data)
        self.assertEqual([p["name"] for p in result["pairs"]], ["vault", "TEMPLATES"])
        self.assertTrue((target / "t.xlsx").is_file())


class ImportTests(Base):
    def setUp(self):
        super().setUp()
        self.note = write(self.src / "A" / "n.md", "v1", OLD)
        write(self.src / "A" / "gone.md", "g", OLD)
        write(self.src / "A" / "both.md", "b1", OLD)
        backup.export_pair(self.pair, self.data)

    def test_preview_kinds_and_defaults(self):
        bump(self.dst / "A" / "n.md", "offline")
        write(self.dst / "A" / "new.md", "new")
        (self.dst / "A" / "gone.md").unlink()
        bump(self.dst / "A" / "both.md", "offline b")
        bump(self.src / "A" / "both.md", "online b", 50)
        items = self.preview()
        self.assertEqual({rel: (i["kind"], i["checked"]) for rel, i in items.items()}, {
            "A/n.md": ("changed", True),
            "A/new.md": ("new", True),
            "A/gone.md": ("deleted", False),
            "A/both.md": ("conflict", True),
        })

    def test_apply_changed_and_new_keeps_old_copy(self):
        bump(self.dst / "A" / "n.md", "offline")
        write(self.dst / "A" / "new.md", "new")
        result = self.apply(["A/n.md", "A/new.md"])
        self.assertEqual(result["failed"], [])
        self.assertEqual(self.note.read_text(encoding="utf-8"), "offline")
        self.assertEqual((self.src / "A" / "new.md").read_text(encoding="utf-8"), "new")
        self.assertEqual((Path(result["kept"]) / "A" / "n.md").read_text(encoding="utf-8"), "v1")
        self.assertEqual(self.preview(), {})
        self.assertEqual(backup.export_pair(self.pair, self.data)["copied"], 0)

    def test_conflict_saved_beside_with_offline_name(self):
        bump(self.dst / "A" / "both.md", "offline b")
        bump(self.src / "A" / "both.md", "online b", 50)
        self.apply(["A/both.md"])
        self.assertEqual((self.src / "A" / "both.md").read_text(encoding="utf-8"), "online b")
        self.assertEqual((self.src / "A" / "both (offline 2026-11-18).md").read_text(encoding="utf-8"), "offline b")
        self.assertEqual((self.dst / "A" / "both.md").read_text(encoding="utf-8"), "online b")
        self.assertEqual(self.preview(), {})

    def test_deleted_only_when_ticked(self):
        (self.dst / "A" / "gone.md").unlink()
        self.apply([])
        self.assertTrue((self.src / "A" / "gone.md").is_file())
        result = self.apply(["A/gone.md"])
        self.assertFalse((self.src / "A" / "gone.md").exists())
        self.assertTrue((Path(result["kept"]) / "A" / "gone.md").is_file())

    def test_unpicked_items_untouched(self):
        bump(self.dst / "A" / "n.md", "offline")
        write(self.dst / "A" / "new.md", "new")
        self.apply(["A/new.md"])
        self.assertEqual(self.note.read_text(encoding="utf-8"), "v1")
        self.assertIn("A/n.md", self.preview())

    def test_stale_pick_does_nothing(self):
        result = self.apply(["A/n.md", "../outside.md"])
        self.assertEqual(result["applied"], [])
        self.assertEqual(self.note.read_text(encoding="utf-8"), "v1")


class ScheduleTests(unittest.TestCase):
    cfg = {"vault": "R:/v", "backup_vault": {"path": "E:/v", "daily_time": "14:00"}}

    def test_parse_time(self):
        self.assertEqual(backup.parse_time("14:00"), (14, 0))
        self.assertEqual(backup.parse_time(" 7:05 "), (7, 5))
        for bad in ("", "25:00", "noon", "14"):
            self.assertIsNone(backup.parse_time(bad))

    def test_due_after_time_once_a_day(self):
        now = datetime(2026, 11, 18, 14, 30)
        self.assertTrue(backup.export_due(self.cfg, {}, now))
        self.assertTrue(backup.export_due(self.cfg, {"last_export_at": "2026-11-18T13:00:00"}, now))
        self.assertFalse(backup.export_due(self.cfg, {"last_export_at": "2026-11-18T14:05:00"}, now))
        self.assertFalse(backup.export_due(self.cfg, {"last_export_at": "2026-11-17T15:00:00"}, datetime(2026, 11, 18, 13, 0)))

    def test_missed_run_catches_up(self):
        status = {"last_export_at": "2026-11-16T14:05:00"}
        self.assertTrue(backup.export_due(self.cfg, status, datetime(2026, 11, 18, 8, 0)))

    def test_not_due_offline_disabled_or_no_time(self):
        now = datetime(2026, 11, 18, 15, 0)
        self.assertFalse(backup.export_due(dict(self.cfg, offline={"active": True}), {}, now))
        self.assertFalse(backup.export_due({"vault": "R:/v", "backup_vault": {"path": "", "daily_time": "14:00"}}, {}, now))
        self.assertFalse(backup.export_due({"vault": "R:/v", "backup_vault": {"path": "E:/v", "daily_time": ""}}, {}, now))


class OfflineTests(unittest.TestCase):
    def cfg(self, **conf):
        base = {"path": "E:/My Projects", "offline_fallback": True,
                "folders": [{"source": "R:/APE_AE/TEMPLATES", "backup": "E:/APE_AE_RDrive/TEMPLATES"}]}
        base.update(conf)
        return {
            "vault": "R:/APE_AE/Me/My Projects",
            "personal_tasks_path": "R:/APE_AE/Me/My Projects/My Tasks.md",
            "templates_root": "R:/APE_AE/TEMPLATES",
            "file_templates": {"pr": {"source": "R:\\APE_AE\\TEMPLATES\\Parts\\PR.xlsx"}},
            "backup_root": "C:/repos/ProjectHub/backups",
            "backup_vault": base,
        }

    def test_online_leaves_paths(self):
        out = backup.apply_offline(self.cfg(), check=lambda p: True)
        self.assertFalse(out["offline"]["active"])
        self.assertEqual(out["vault"], "R:/APE_AE/Me/My Projects")

    def test_auto_switch_when_r_unreachable(self):
        out = backup.apply_offline(self.cfg(), check=lambda p: not str(p).startswith("R:"))
        self.assertTrue(out["offline"]["active"])
        self.assertEqual(Path(out["vault"]), Path("E:/My Projects"))
        self.assertEqual(Path(out["personal_tasks_path"]), Path("E:/My Projects/My Tasks.md"))
        self.assertEqual(Path(out["templates_root"]), Path("E:/APE_AE_RDrive/TEMPLATES"))
        self.assertEqual(Path(out["file_templates"]["pr"]["source"]), Path("E:/APE_AE_RDrive/TEMPLATES/Parts/PR.xlsx"))
        self.assertEqual(out["backup_root"], "C:/repos/ProjectHub/backups")
        self.assertEqual(out["online_vault"], "R:/APE_AE/Me/My Projects")
        self.assertEqual(out["backup_vault"]["folders"][0]["source"], "R:/APE_AE/TEMPLATES")
        self.assertEqual(backup.pairs(out)[0]["source"], "R:/APE_AE/Me/My Projects")

    def test_auto_switch_can_be_turned_off(self):
        out = backup.apply_offline(self.cfg(offline_fallback=False), check=lambda p: not str(p).startswith("R:"))
        self.assertFalse(out["offline"]["active"])

    def test_forced_offline(self):
        out = backup.apply_offline(self.cfg(work_offline=True), check=lambda p: True)
        self.assertTrue(out["offline"]["active"])
        self.assertTrue(out["offline"]["forced"])
        self.assertEqual(Path(out["vault"]), Path("E:/My Projects"))

    def test_both_unreachable_stays_online_with_reason(self):
        out = backup.apply_offline(self.cfg(), check=lambda p: False)
        self.assertFalse(out["offline"]["active"])
        self.assertIn("both unreachable", out["offline"]["reason"])

    def test_no_backup_configured(self):
        out = backup.apply_offline({"vault": "R:/v"}, check=lambda p: False)
        self.assertFalse(out["offline"]["active"])
        self.assertEqual(out["vault"], "R:/v")

    def test_settings_overlay_backup_keys(self):
        merged = settings_mod.apply_overlay(
            self.cfg(), {"backup": {"work_offline": True, "offline_fallback": False, "daily_time": "09:30", "path": "X:/"}}
        )
        conf = merged["backup_vault"]
        self.assertTrue(conf["work_offline"])
        self.assertFalse(conf["offline_fallback"])
        self.assertEqual(conf["daily_time"], "09:30")
        self.assertEqual(conf["path"], "E:/My Projects")


class SetupTests(unittest.TestCase):
    def test_gui_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            base = {"vault": str(vault), "backup_vault": {"path": "E:/My Projects", "daily_time": "14:00", "work_offline": True,
                                                         "folders": [{"source": "R:/T", "backup": "E:/T"}]}}
            form = setup_gui.form_from_config(base)
            self.assertEqual(form["backup_folders"], "R:/T => E:/T")
            form.update(name="A", email="", initials="", outlook_enabled=False, scan_project_folders=False)
            form["backup_daily_time"] = "15:30"
            form["backup_folders"] = "R:/T => E:/T\nR:/U => E:/U\n"
            cfg, errors, _ = setup_gui.build_config(form, base)
            self.assertEqual(errors, [])
            conf = cfg["backup_vault"]
            self.assertEqual(conf["daily_time"], "15:30")
            self.assertTrue(conf["work_offline"])
            self.assertEqual(conf["folders"], [{"source": "R:/T", "backup": "E:/T"}, {"source": "R:/U", "backup": "E:/U"}])
            form["backup_daily_time"] = "noon"
            form["backup_folders"] = "R:/T"
            _, errors, _ = setup_gui.build_config(form, base)
            self.assertEqual(len(errors), 2)

    def test_wizard_backup_prompts(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            replies = iter(["A", "a@x", "AA", str(vault), "", "", "", "", "9100", "E:/Backup", "25:00", "13:15"])
            answers = setup_wizard.prompt_answers({}, ask=lambda prompt: next(replies))
            cfg = setup_wizard.build_config({}, answers)
            self.assertEqual(cfg["backup_vault"]["path"], "E:/Backup")
            self.assertEqual(cfg["backup_vault"]["daily_time"], "13:15")
            self.assertTrue(cfg["backup_vault"]["offline_fallback"])
            self.assertEqual(setup_wizard.stored_answers(cfg)["backup_path"], "E:/Backup")


class HubBackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "R" / "vault"
        self.copy = self.root / "E" / "vault"
        write(self.vault / "Acme" / "SO-1" / "SO-1.md", "---\nso: [SO-1]\n---\n", OLD)
        self.data = self.root / "data"
        self.config_path = self.root / "config.yaml"
        self.cfg = {
            "vault": str(self.vault),
            "state_path": str(self.root / "state.json"),
            "snapshot_path": str(self.root / "snapshot.json"),
            "log_path": str(self.root / "last_run.log"),
            "backup_root": str(self.root / "backups"),
            "backup_data_dir": str(self.data),
            "templates_dir": str(self.root / "templates_out"),
            "rules_path": str(RULES),
            "me": {"name": "Test User"},
            "hub": {"port": 8765, "refresh_minutes": 15, "toast": False},
            "tools": {"wire_diagram_builder": ""},
            "backup_vault": {"path": str(self.copy), "daily_time": "14:00", "offline_fallback": True},
        }
        (self.root / "snapshot.json").write_text(json.dumps({
            "generated": "2026-11-18T06:30:00-08:00", "today": "2026-11-18", "projects": [], "pending": [], "unmatched": [],
        }), encoding="utf-8")
        self.runs = []
        self.make_app()

    def tearDown(self):
        self.tmp.cleanup()

    def make_app(self):
        self.config_path.write_text(yaml.safe_dump(self.cfg), encoding="utf-8")
        self.app = create_app(self.config_path, port=8765, no_refresh=True, runner=lambda: self.runs.append(1) or 0)
        self.app.testing = True

    def post(self, url, payload):
        headers = dict(HOST, Origin=ORIGIN)
        headers["X-Hub-Token"] = self.app.config["HUB_TOKEN"]
        return self.app.test_client().post(url, json=payload, headers=headers)

    def get(self, url):
        return self.app.test_client().get(url, headers=HOST)

    def wait_export(self):
        for _ in range(200):
            job = self.get("/api/backup/status").get_json()["job"]
            if not job["running"]:
                return job
            time.sleep(0.05)
        self.fail("backup did not finish")

    def test_export_preview_apply(self):
        self.assertIn("Not backed up yet", self.get("/settings").get_data(as_text=True))
        self.assertEqual(self.post("/api/backup/export", {}).status_code, 200)
        job = self.wait_export()
        self.assertEqual(job["result"]["errors"], [])
        self.assertTrue((self.copy / "Acme" / "SO-1" / "SO-1.md").is_file())
        page = self.get("/settings").get_data(as_text=True)
        self.assertIn("Backed up", page)
        write(self.copy / "Acme" / "SO-1" / "offline.md", "x")
        items = self.post("/api/backup/import/preview", {}).get_json()["items"]
        self.assertEqual([(i["rel"], i["kind"]) for i in items], [("Acme/SO-1/offline.md", "new")])
        result = self.post("/api/backup/import/apply", {"items": [{"pair": "vault", "rel": "Acme/SO-1/offline.md"}]}).get_json()
        self.assertTrue(result["ok"])
        self.assertTrue((self.vault / "Acme" / "SO-1" / "offline.md").is_file())
        self.assertEqual(self.runs, [1])
        self.assertEqual(self.post("/api/backup/import/apply", {"items": []}).status_code, 400)

    def test_work_offline_switch_banner_and_blocks(self):
        self.copy.mkdir(parents=True)
        response = self.post("/api/settings", {"backup": {"work_offline": True}})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        page = self.get("/settings").get_data(as_text=True)
        self.assertIn("offline-banner", page)
        self.assertIn("side-backup-bad", page)
        self.assertEqual(self.post("/api/backup/export", {}).status_code, 409)
        self.assertEqual(self.post("/api/backup/import/preview", {}).status_code, 409)
        self.assertEqual(self.post("/api/settings", {"backup": {"daily_time": "nope"}}).status_code, 400)
        self.post("/api/settings", {"backup": {"work_offline": False}})
        page = self.get("/settings").get_data(as_text=True)
        self.assertIn("backup-dismiss", page)
        self.assertEqual(self.post("/api/backup/dismiss", {}).status_code, 200)
        self.assertNotIn('data-action="backup-dismiss"', self.get("/settings").get_data(as_text=True))

    def test_auto_offline_when_vault_missing(self):
        self.copy.mkdir(parents=True)
        self.cfg["vault"] = str(self.root / "R" / "missing")
        self.make_app()
        page = self.get("/").get_data(as_text=True)
        self.assertIn("offline-banner", page)


if __name__ == "__main__":
    unittest.main()
