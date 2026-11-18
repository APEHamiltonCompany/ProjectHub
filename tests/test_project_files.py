from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tracker import extract, matcher, notes, project_files as pf
from tracker import projects as projects_mod
from tracker.hub.app import create_app
from tracker.models import Email
from tests.test_file_templates import NOTE_TEXT, FileTemplateTests
from tests.test_hub import HOST, ORIGIN

EXAMPLE = ROOT / "tracker" / "config.example.yaml"


def make_templates(root: Path) -> Path:
    """A fake R:/APE_AE/TEMPLATES tree with the shapes found on the real share."""
    files = {
        "Time Estimate/TimeEstimates.xlsx": b"te",
        "Parts Request/PartsRequestSO-XXXXXXX.xlsx": b"pr",
        "Design Review Test Matrix/DesignReview_TestMatrix_L1Carrier.pptx": b"l1",
        "Design Review Test Matrix/DesignReview_TestMatrix_L2L3.pptx": b"l2l3",
        "Generic Test Checklists/APE Custom 020XXXX Compliance_V1.0.3.docx": b"comp",
        "Generic Test Checklists/~$E Custom 020XXXX Compliance_V1.0.3.docx": b"lock",
        "Apps Plan/Applications Development Plan Template v3.5.docx": b"v35",
        "Apps Plan/Applications Development Plan Template v3.6.docx": b"v36",
        "Apps Plan/Applications Development Plan Template v3.6 (1).docx": b"dup",
    }
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    # the duplicate is the newest file; it must still lose to the real v3.6
    later = time.time() + 60
    os.utime(root / "Apps Plan/Applications Development Plan Template v3.6 (1).docx", (later, later))
    return root


def example_project_files() -> dict:
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))["project_files"]


TOKENS = pf.tokens_for("Acme", ["SO-297037"], [], "0201234")


class ProjectFilesCoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = {"templates_root": str(make_templates(self.root / "tpl")), "project_files": example_project_files()}
        self.folder = self.root / "proj"
        self.folder.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def item(self, item_id):
        return pf.item_by_id(self.cfg, item_id)

    def test_names(self):
        names = {item["id"]: pf.dest_name(item, TOKENS) for item in pf.specs(self.cfg)[0]}
        self.assertEqual(
            names,
            {
                "time_estimate": "TimeEstimates SO-297037.xlsx",
                "parts_request": "PartsRequestSO-297037.xlsx",
                "test_matrix_l1": "DesignReview_TestMatrix_L1Carrier SO-297037.pptx",
                "test_matrix_l2l3": "DesignReview_TestMatrix_L2L3 SO-297037.pptx",
                "compliance": "APE Custom 0201234 Compliance_V1.0.0.docx",
                "apps_plan": "Applications Development Plan SO-297037.docx",
            },
        )

    def test_compliance_needs_project_number_only(self):
        self.assertEqual(
            pf.dest_name(self.item("compliance"), pf.tokens_for("Acme", [], [], "0201234")),
            "APE Custom 0201234 Compliance_V1.0.0.docx",
        )
        with self.assertRaisesRegex(ValueError, "project number"):
            pf.dest_name(self.item("compliance"), pf.tokens_for("Acme", ["SO-1"], [], ""))

    def test_ident_falls_back_to_qte_then_project_no(self):
        item = self.item("time_estimate")
        self.assertEqual(pf.dest_name(item, pf.tokens_for("A", [], ["QTE-9"], "0201234")), "TimeEstimates QTE-9.xlsx")
        self.assertEqual(pf.dest_name(item, pf.tokens_for("A", [], [], "0201234")), "TimeEstimates 0201234.xlsx")

    def test_source_picks_highest_version_and_skips_lock_and_duplicates(self):
        self.assertEqual(pf.find_source(self.cfg, self.item("apps_plan")).name, "Applications Development Plan Template v3.6.docx")
        self.assertEqual(pf.find_source(self.cfg, self.item("compliance")).name, "APE Custom 020XXXX Compliance_V1.0.3.docx")

    def test_never_overwrites(self):
        path, created = pf.ensure_file(self.cfg, self.item("time_estimate"), self.folder, TOKENS)
        self.assertTrue(created)
        path.write_bytes(b"edited")
        again, created = pf.ensure_file(self.cfg, self.item("time_estimate"), self.folder, TOKENS)
        self.assertFalse(created)
        self.assertEqual(again.read_bytes(), b"edited")
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["TimeEstimates SO-297037.xlsx"])

    def test_missing_source_is_skipped(self):
        (self.root / "tpl/Parts Request/PartsRequestSO-XXXXXXX.xlsx").unlink()
        created, skipped = pf.copy_files(self.cfg, self.folder, TOKENS, pf.selected_items(self.cfg, {}))
        self.assertEqual([Path(p).name for p in created], ["TimeEstimates SO-297037.xlsx"])
        self.assertEqual(len(skipped), 1)
        self.assertIn("Parts request", skipped[0])

    def test_validate_choices(self):
        picked, errors = pf.validate_choices(self.cfg, {"test_matrix": "None", "project_document": "Compliance"})
        self.assertEqual(errors, [])
        self.assertEqual(picked, {"test_matrix": "none", "project_document": "compliance"})
        _picked, errors = pf.validate_choices(self.cfg, {"test_matrix": "L2L3", "project_document": "none"})
        self.assertEqual(errors, ["A project document is required"])
        _picked, errors = pf.validate_choices(self.cfg, {})
        self.assertEqual(errors, ["Choose a test matrix", "Choose a project document"])

    def test_legacy_time_estimate_template(self):
        cfg = {"time_estimate_template": str(self.root / "tpl/Time Estimate/TimeEstimates.xlsx")}
        items, groups = pf.specs(cfg)
        self.assertEqual(groups, {})
        created, skipped = pf.copy_files(cfg, self.folder, TOKENS, items)
        self.assertEqual(skipped, [])
        self.assertEqual([Path(p).name for p in created], ["TimeEstimates SO-297037.xlsx"])


class CreateWithProjectFilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        vault = self.root / "vault"
        vault.mkdir()
        self.cfg = {
            "vault": str(vault),
            "templates_dir": str(ROOT / "templates"),
            "templates_root": str(make_templates(self.root / "tpl")),
            "project_files": example_project_files(),
            "holidays": [],
            "rules_path": str(ROOT / "tracker" / "rules.yaml"),
        }

    def tearDown(self):
        self.tmp.cleanup()

    def entry(self) -> dict:
        email = Email(
            message_id="exact",
            subject="Project Assignment - Exact | SO-296266 | 0207840 | STAR23 | L2",
            sender="APE admin", to="", cc="",
            date=datetime(2026, 11, 6, 10, 1, tzinfo=timezone.utc),
            body_text="Sharepoint Link <https://example.com/exact>\r\n",
        )
        return projects_mod.pending_entry(
            email, matcher.parse_assignment_subject(email.subject), extract.parse_assignment_body(email.body_text)
        )

    def files(self, info) -> list[str]:
        return sorted(p.name for p in Path(info["folder"]).iterdir() if not p.name.endswith(".md"))

    def frontmatter(self, info) -> dict:
        return notes.split_frontmatter(notes.read_text(info["note_path"])[0])[0]

    def test_create_with_choices(self):
        info = projects_mod.create_from_pending(
            self.cfg, self.entry(), choices={"test_matrix": "test_matrix_l2l3", "project_document": "compliance"}
        )
        self.assertEqual(
            self.files(info),
            [
                "APE Custom 0207840 Compliance_V1.0.0.docx",
                "DesignReview_TestMatrix_L2L3 SO-296266.pptx",
                "PartsRequestSO-296266.xlsx",
                "TimeEstimates SO-296266.xlsx",
            ],
        )
        self.assertEqual(info["files_skipped"], [])
        data = self.frontmatter(info)
        self.assertEqual(data["test_matrix"], "L2L3")
        self.assertEqual(data["project_document"], "Compliance")

    def test_create_with_no_test_matrix(self):
        info = projects_mod.create_from_pending(
            self.cfg, self.entry(), choices={"test_matrix": "none", "project_document": "apps_plan"}
        )
        self.assertEqual(
            self.files(info),
            ["Applications Development Plan SO-296266.docx", "PartsRequestSO-296266.xlsx", "TimeEstimates SO-296266.xlsx"],
        )
        self.assertEqual(self.frontmatter(info)["test_matrix"], "None")

    def test_unanswered_choice_refuses_before_creating(self):
        with self.assertRaisesRegex(ValueError, "project document"):
            projects_mod.create_from_pending(self.cfg, self.entry(), choices={"test_matrix": "none"})
        self.assertEqual(list(Path(self.cfg["vault"]).iterdir()), [])

    def test_automatic_creation_adds_pick_task(self):
        info = projects_mod.create_from_pending(self.cfg, self.entry())
        self.assertEqual(self.files(info), ["PartsRequestSO-296266.xlsx", "TimeEstimates SO-296266.xlsx"])
        text = notes.read_text(info["note_path"])[0]
        self.assertIn("Choose test matrix and project document on the project page (Project files)", text)
        self.assertNotIn("test_matrix", self.frontmatter(info))


class ProjectFilesHubTests(unittest.TestCase):
    """Hub endpoints, reusing the fixture project from test_file_templates."""

    build_app_base = FileTemplateTests.build_app
    project = FileTemplateTests.project
    snapshot = FileTemplateTests.snapshot
    templates = FileTemplateTests.templates

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.folder = self.vault / "Acme SO-1 0207000"
        self.folder.mkdir()
        self.note = self.folder / "Acme SO-1 0207000.md"
        self.note.write_text(NOTE_TEXT, encoding="utf-8")
        self.source = self.root / "unused.xlsx"
        self.source.write_bytes(b"x")
        self.tpl = make_templates(self.root / "tpl")
        self.state_path = self.root / "state.json"
        self.snapshot_path = self.root / "snapshot.json"
        self.config_path = self.root / "config.yaml"
        self.launched = []
        self.app = self.build_app("file:test_matrix")

    def tearDown(self):
        self.tmp.cleanup()

    def build_app(self, launch: str):
        app = self.build_app_base(launch=launch)
        cfg = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        cfg["templates_root"] = str(self.tpl)
        cfg["project_files"] = example_project_files()
        cfg["templates_dir"] = str(ROOT / "templates")
        self.config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        app = create_app(self.config_path, port=8765, no_refresh=True, runner=lambda: 0)
        app.testing = True
        app.config["LAUNCHER"] = self.launched.append
        return app

    def post(self, url: str, payload: dict):
        headers = dict(HOST)
        headers["X-Hub-Token"] = self.app.config["HUB_TOKEN"]
        headers["Origin"] = ORIGIN
        return self.app.test_client().post(url, json=payload, headers=headers)

    def launch(self):
        return self.post("/api/task/launch", {"key": "0207000", "task_id": "parts_for_testing"})

    def test_page_shows_project_files_panel(self):
        html = self.app.test_client().get("/p/0207000", headers=HOST).get_data(as_text=True)
        self.assertIn("Project files", html)
        self.assertIn("TimeEstimates SO-1.xlsx", html)
        self.assertIn('data-group="project_document"', html)

    def test_open_creates_then_reuses(self):
        response = self.post("/api/projects/files/open", {"key": "0207000", "item": "time_estimate"})
        self.assertEqual(response.status_code, 200)
        dest = self.folder / "TimeEstimates SO-1.xlsx"
        self.assertEqual(response.get_json()["created"], [str(dest)])
        dest.write_bytes(b"edited")
        response = self.post("/api/projects/files/open", {"key": "0207000", "item": "time_estimate"})
        self.assertEqual(response.get_json()["existing"], [str(dest)])
        self.assertEqual(dest.read_bytes(), b"edited")
        self.assertEqual(self.launched, [str(dest), str(dest)])

    def test_task_launch_needs_choice_then_opens_it(self):
        response = self.launch()
        self.assertEqual(response.status_code, 400)
        self.assertIn("Choose a test matrix", response.get_json()["error"])
        response = self.post("/api/projects/files/choose", {"key": "0207000", "group": "test_matrix", "option": "L1Carrier"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("test_matrix: L1Carrier", self.note.read_text(encoding="utf-8"))
        response = self.launch()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["launched"], str(self.folder / "DesignReview_TestMatrix_L1Carrier SO-1.pptx"))

    def test_none_choice(self):
        self.post("/api/projects/files/choose", {"key": "0207000", "group": "test_matrix", "option": "none"})
        response = self.launch()
        self.assertEqual(response.status_code, 400)
        self.assertIn("None", response.get_json()["error"])
        response = self.post("/api/projects/files/choose", {"key": "0207000", "group": "project_document", "option": "none"})
        self.assertEqual(response.status_code, 400)

    def test_create_endpoint_requires_choices(self):
        payload = {"customer": "Beta", "so": "SO-77", "project_no": "0201111"}
        response = self.post("/api/projects/create", payload)
        self.assertEqual(response.status_code, 400)
        payload["choices"] = {"test_matrix": "none", "project_document": "compliance"}
        response = self.post("/api/projects/create", payload)
        self.assertEqual(response.status_code, 200, response.get_json())
        data = response.get_json()
        self.assertEqual(data["files_skipped"], [])
        names = sorted(p.name for p in Path(data["folder"]).iterdir())
        self.assertIn("APE Custom 0201111 Compliance_V1.0.0.docx", names)


if __name__ == "__main__":
    unittest.main()
