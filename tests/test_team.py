import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import yaml

from tracker import team
from tracker.hub.app import create_app

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "tracker" / "rules.yaml"
TODAY = date(2026, 11, 23)


def snapshot(*projects):
    return {"generated": "2026-11-23T10:00:00-08:00", "projects": list(projects)}


def project(key, folder, **extra):
    row = {
        "key": key,
        "name": "Acme " + key,
        "customer": "Acme",
        "so": [key],
        "qte": [],
        "project_no": "",
        "stage": "Design",
        "folder": folder,
        "tasks": [
            {"id": "a", "title": "Late task", "due": "2026-11-01", "mark": " ", "start": "2026-10-20"},
            {"id": "b", "title": "Next task", "due": "2026-12-05", "mark": " "},
            {"id": "c", "title": "Done", "due": "2026-10-01", "mark": "x"},
        ],
    }
    row.update(extra)
    return row


class TeamBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.shared = self.root / "shared"
        self.shared.mkdir()

    def cfg(self, name, email="", **team_conf):
        vault = self.root / ("vault_" + name.split()[0].lower())
        vault.mkdir(exist_ok=True)
        conf = {"publish": True, "shared_dir": str(self.shared)}
        conf.update(team_conf)
        return {"vault": str(vault), "me": {"name": name, "email": email}, "team": conf}

    def set_role(self, name, role):
        rows = team.read_roster(self.cfg(name))
        for row in rows:
            if row["name"] == name:
                row["role"] = role
        (self.shared / team.ROSTER_NAME).write_text(team.roster_text(rows), encoding="utf-8")


class PublishTests(TeamBase):
    def test_first_publish_joins_roster_and_writes_status(self):
        cfg = self.cfg("Cristian Uribe", "c@x.com")
        result = team.publish(cfg, snapshot(project("SO-1", "f1"), project("SO-2", "f2")), {"SO-2": {}}, TODAY)
        self.assertTrue(result["ok"])
        roster = team.read_roster(cfg)
        self.assertEqual([r["name"] for r in roster], ["Cristian Uribe"])
        status = json.loads((self.shared / "Cristian Uribe" / "status.json").read_text(encoding="utf-8"))
        self.assertEqual([p["key"] for p in status["projects"]], ["SO-1"])
        row = status["projects"][0]
        self.assertEqual(row["overdue"], [{"title": "Late task", "due": "2026-11-01"}])
        self.assertEqual(row["next_due"]["title"], "Late task")
        self.assertEqual((row["tasks_done"], row["tasks_total"]), (1, 3))
        self.assertEqual((row["start"], row["finish"], row["dates_from"]), ("2026-10-20", "2026-12-05", "tasks"))
        js = (self.shared / "Cristian Uribe" / "status.js").read_text(encoding="utf-8")
        self.assertIn('window.TEAM_STATUS["Cristian Uribe"]', js)
        self.assertTrue((self.shared / "Team Status.html").is_file())
        self.assertTrue((self.shared / "team_view.js").is_file())

    def test_roster_appends_new_hire_and_keeps_roles(self):
        team.publish(self.cfg("Cristian Uribe"), snapshot(), {}, TODAY)
        self.set_role("Cristian Uribe", "manager")
        team.publish(self.cfg("New Hire"), snapshot(), {}, TODAY)
        team.publish(self.cfg("Cristian Uribe"), snapshot(), {}, TODAY)
        rows = team.read_roster(self.cfg("x"))
        self.assertEqual([(r["name"], r["role"]) for r in rows], [("Cristian Uribe", "manager"), ("New Hire", "")])

    def test_schedule_window_and_pm_win(self):
        row = project("SO-1", "f1", schedule={"pm": "CD (L1R)", "start": "2026-06-16", "finish": "2026-09-23", "level": "L1R"})
        status = team.build_status(self.cfg("A B"), snapshot(row), {}, TODAY)
        out = status["projects"][0]
        self.assertEqual((out["pm"], out["start"], out["finish"], out["dates_from"]), ("Chris Derby", "2026-06-16", "2026-09-23", "schedule"))

    def test_known_pms_written_once_and_hand_edits_kept(self):
        cfg = self.cfg("Cristian Uribe")
        team.publish(cfg, snapshot(), {}, TODAY)
        self.assertEqual(team.file_pms(team.read_team_file(cfg)), team.KNOWN_PMS)
        rows = team.read_roster(cfg)
        pms = dict(team.KNOWN_PMS, CD="Chris D.", ZZ="New PM")
        (self.shared / team.ROSTER_NAME).write_text(team.roster_text(rows, pms), encoding="utf-8")
        team.publish(self.cfg("New Hire"), snapshot(), {}, TODAY)
        self.assertEqual(team.file_pms(team.read_team_file(cfg)), pms)
        self.assertEqual([r["name"] for r in team.read_roster(cfg)], ["Cristian Uribe", "New Hire"])
        self.assertEqual(team.pm_name("ZZ", team.read_pms(cfg)), "New PM")
        self.assertEqual(team.pm_name("XY (L2)", team.read_pms(cfg)), "XY")

    def test_nothing_published_offline_or_unreachable_or_off(self):
        cfg = dict(self.cfg("A B"), offline={"active": True})
        self.assertFalse(team.publish(cfg, snapshot(), {}, TODAY)["ok"])
        cfg = self.cfg("A B", shared_dir=str(self.root / "missing"))
        self.assertFalse(team.publish(cfg, snapshot(), {}, TODAY)["ok"])
        cfg = self.cfg("A B", publish=False)
        self.assertFalse(team.publish(cfg, snapshot(), {}, TODAY)["ok"])
        self.assertFalse((self.shared / team.ROSTER_NAME).exists())


class ReassignTests(TeamBase):
    def setUp(self):
        super().setUp()
        self.old = self.cfg("Old Owner")
        self.new = self.cfg("New Owner")
        self.boss = self.cfg("Team Manager")
        self.folder = Path(self.old["vault"]) / "Acme" / "SO-1"
        self.folder.mkdir(parents=True)
        (self.folder / "SO-1.md").write_text("note", encoding="utf-8")
        for cfg in (self.old, self.new, self.boss):
            team.publish(cfg, snapshot(), {}, TODAY)
        self.row = project("SO-1", str(self.folder))

    def test_only_manager_or_principal_can_reassign(self):
        with self.assertRaises(PermissionError):
            team.write_request(self.boss, self.row, "Old Owner", "New Owner")
        self.set_role("Old Owner", "")
        self.set_role("New Owner", "principal")
        team.write_request(self.new, self.row, "Old Owner", "New Owner")

    def test_forged_request_is_ignored(self):
        (self.shared / team.REQUESTS_DIR).mkdir()
        forged = {"id": "x1", "key": "SO-1", "from": "Old Owner", "to": "New Owner", "by": "Old Owner"}
        (self.shared / team.REQUESTS_DIR / "x1.json").write_text(json.dumps(forged), encoding="utf-8")
        self.assertEqual(team.my_transfers(self.new)["incoming"], [])

    def test_flow_flags_both_owners_and_imports_without_overwrite(self):
        self.set_role("Team Manager", "manager")
        request = team.write_request(self.boss, self.row, "Old Owner", "New Owner")
        incoming = team.my_transfers(self.new)["incoming"]
        outgoing = team.my_transfers(self.old)["outgoing"]
        self.assertEqual([r["id"] for r in incoming], [request["id"]])
        self.assertEqual([r["id"] for r in outgoing], [request["id"]])
        self.assertEqual(outgoing[0]["state"], "pending")
        result = team.import_transfer(self.new, incoming[0])
        target = Path(result["folder"])
        self.assertEqual((target / "SO-1.md").read_text(encoding="utf-8"), "note")
        self.assertTrue(self.folder.is_dir())
        self.assertEqual(team.my_transfers(self.old)["outgoing"][0]["state"], "imported")
        self.assertTrue(team.my_transfers(self.new)["incoming"][0]["replied"])
        with self.assertRaises(FileExistsError):
            team.import_transfer(self.new, incoming[0])
        published = snapshot(project("SO-1", str(self.folder)))
        team.publish(self.old, published, {}, TODAY)
        status = json.loads((self.shared / "Old Owner" / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["projects"][0]["reassigned_to"], "New Owner")

    def test_reassign_blocked_when_r_is_down(self):
        self.set_role("Team Manager", "manager")
        with self.assertRaises(OSError):
            team.write_request(dict(self.boss, offline={"active": True}), self.row, "Old Owner", "New Owner")


class FlagTests(TeamBase):
    def test_owner_flags(self):
        cfg = self.cfg("Cristian Uribe")
        row = {"sheet": {"ae_name": "Travis Davidson"}}
        flags = team.owner_flags(cfg, row, [{"name": "Travis Davidson"}], False)
        self.assertEqual(len(flags), 2)
        self.assertIn("Travis Davidson", flags[0])
        self.assertEqual(team.owner_flags(cfg, {"sheet": {"ae_name": "Cristian Uribe"}}, [], True), [])
        self.assertEqual(team.owner_flags(cfg, {"sheet": {"ae_name": "JI"}}, [], None), [])


class HubTeamTests(TeamBase):
    def make_app(self, cfg):
        cfg = dict(cfg)
        cfg.update(
            state_path=str(self.root / "state.json"),
            snapshot_path=str(self.root / "snapshot.json"),
            rules_path=str(RULES),
            ape_sheet={"enabled": False},
        )
        path = self.root / "config.yaml"
        path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        (self.root / "snapshot.json").write_text(json.dumps(snapshot()), encoding="utf-8")
        app = create_app(path, port=8765, no_refresh=True, runner=lambda: 0)
        app.config["TESTING"] = True
        return app

    def test_team_page_hidden_without_role_and_reassign_requires_role(self):
        team.publish(self.cfg("Old Owner"), snapshot(project("SO-1", "f")), {}, TODAY)
        team.publish(self.cfg("New Owner"), snapshot(), {}, TODAY)
        app = self.make_app(self.cfg("New Owner"))
        client = app.test_client()
        client.environ_base["HTTP_HOST"] = "127.0.0.1:8765"
        client.get = lambda url, _get=client.get: _get(url, base_url="http://127.0.0.1:8765")
        self.assertEqual(client.get("/team").status_code, 404)
        self.set_role("New Owner", "principal")
        page = client.get("/team")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"TeamView.render", page.data)
        self.assertIn(b"team-reassign-box", page.data)
        self.assertEqual(client.get("/team/view.js").status_code, 200)

    def test_manager_mode_is_view_only(self):
        team.publish(self.cfg("Old Owner"), snapshot(project("SO-1", "f")), {}, TODAY)
        app = self.make_app(self.cfg("Viewer", manager_mode=True))
        client = app.test_client()
        client.environ_base["HTTP_HOST"] = "127.0.0.1:8765"
        client.get = lambda url, _get=client.get: _get(url, base_url="http://127.0.0.1:8765")
        page = client.get("/team")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(b"team-reassign-box", page.data)
        self.assertNotIn(b'data-action="team-reassign"', page.data)


if __name__ == "__main__":
    unittest.main()
