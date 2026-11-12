from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tracker import notes
from tracker import projects as projects_mod
from tracker.hub.app import create_app

HOST = {"Host": "127.0.0.1:8765"}
ORIGIN = "http://127.0.0.1:8765"
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
- [ ] Review CPR/SO \U0001F4C5 2026-11-02 \U0001F194 p0207000-review_cpr_so
- [x] Time estimate \U0001F4C5 2026-11-02 \U0001F194 p0207000-time_estimate
<!-- tracker:end -->
"""
TEMPLATE_TEXT = """---
project_no: "{project_no}"
so: [{so_yaml}]
qte: [{qte_yaml}]
customer: "{customer}"
level: "{level}"
stage: "{stage}"
assigned: "{assigned}"
sharepoint: "{sharepoint}"
team: [{team_yaml}]
---

**{date_ddMonYYYY}**

**Project Assignment - {subject}**

**TO DO:** (Mark done items with #)

**AE Order Review Checklist:**
{checklist}
- [ ] Review the pipettor configuration
"""
PENDING = {
    "message_id": "msg-1",
    "subject": "Project Assignment - Acme | SO-222222 | 0207999 | Widget | L1",
    "received": "2026-11-09T08:00:00-08:00",
    "parsed": {
        "customer": "Acme",
        "so": ["SO-222222"],
        "qte": [],
        "project_no": "0207999",
        "level": "L1",
        "description": "Widget",
    },
    "sharepoint": "https://example.com/new",
    "team": ["Chris Holtz"],
    "checklist": ["Check the SO"],
    "suggested_folder": "Acme SO-222222 0207999",
    "status": "new",
}


def sha256_bytes(path: Path) -> str:
    return hashlib.sha256(notes.lp(path).read_bytes()).hexdigest()


class LinkPendingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.templates = self.root / "templates"
        self.templates.mkdir()
        (self.templates / "Project Template.md").write_text(TEMPLATE_TEXT, encoding="utf-8")
        self.cfg = {
            "vault": str(self.vault),
            "templates_dir": str(self.templates),
            "rules_path": str(ROOT / "tracker" / "rules.yaml"),
            "holidays": [],
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_merges_only_empty_frontmatter_keys(self):
        folder = self.vault / "Acme SO-222222 0207999"
        folder.mkdir()
        note = folder / "Acme SO-222222 0207999.md"
        body = "\n**TO DO:** (Mark done items with #)\n\nkeep this body\n"
        note.write_text(
            '---\ncustomer: "Existing Co"\nproject_no: ""\nlevel: ""\n---\n' + body,
            encoding="utf-8",
        )
        info = projects_mod.link_pending(self.cfg, PENDING, str(folder))
        self.assertEqual(Path(info["note_path"]), note)
        self.assertFalse(info["created"])
        text, _nl, _bom, _enc = notes.read_text(note)
        data, _raw, new_body = notes.split_frontmatter(text)
        self.assertEqual(data["customer"], "Existing Co")
        self.assertEqual(data["project_no"], "0207999")
        self.assertEqual(data["level"], "L1")
        self.assertEqual(data["so"], ["SO-222222"])
        self.assertEqual(data["team"], ["Chris Holtz"])
        self.assertEqual(data["sharepoint"], "https://example.com/new")
        self.assertIn("keep this body", new_body)

    def test_creates_note_when_folder_has_none(self):
        folder = self.vault / "Empty Folder"
        folder.mkdir()
        info = projects_mod.link_pending(self.cfg, PENDING, "Empty Folder")
        self.assertTrue(info["created"])
        note = Path(info["note_path"])
        self.assertTrue(note.is_file())
        text, _nl, _bom, _enc = notes.read_text(note)
        data, _raw, body = notes.split_frontmatter(text)
        self.assertEqual(data["customer"], "Acme")
        self.assertEqual(data["project_no"], "0207999")
        self.assertIn("Review CPR/SO", body)
        self.assertEqual(sorted(p.name for p in folder.iterdir()), [note.name])

    def test_rejects_folder_outside_vault(self):
        outside = self.root / "outside"
        outside.mkdir()
        with self.assertRaises(ValueError):
            projects_mod.link_pending(self.cfg, PENDING, str(outside))
        with self.assertRaises(ValueError):
            projects_mod.link_pending(self.cfg, PENDING, "../outside")

    def test_rejects_missing_folder(self):
        with self.assertRaises(ValueError):
            projects_mod.link_pending(self.cfg, PENDING, "Nope")


class HubAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.templates = self.root / "templates"
        self.templates.mkdir()
        (self.templates / "Project Template.md").write_text(TEMPLATE_TEXT, encoding="utf-8")
        self.folder = self.vault / "Acme SO-1 0207000"
        self.folder.mkdir()
        self.note = self.folder / "Acme SO-1 0207000.md"
        self.note.write_text(NOTE_TEXT, encoding="utf-8")
        self.state_path = self.root / "state.json"
        self.snapshot_path = self.root / "snapshot.json"
        self.eml_path = self.root / "sample.eml"
        self.eml_path.write_text("Subject: Acme closeout\n\nbody\n", encoding="utf-8")
        self.config_path = self.root / "config.yaml"
        self.cfg = {
            "vault": str(self.vault),
            "exclude_folders": ["General"],
            "state_path": str(self.state_path),
            "snapshot_path": str(self.snapshot_path),
            "log_path": str(self.root / "last_run.log"),
            "backup_root": str(self.root / "backups"),
            "templates_dir": str(self.templates),
            "rules_path": str(ROOT / "tracker" / "rules.yaml"),
            "hub": {"port": 8765, "refresh_minutes": 15, "toast": False},
        }
        self.config_path.write_text(yaml.safe_dump(self.cfg), encoding="utf-8")
        self.state_path.write_text(
            json.dumps(
                {
                    "pending": {"msg-1": dict(PENDING)},
                    "unmatched": [],
                    "email_overrides": {},
                    "due_overrides": {},
                    "tick_log": [],
                    "user_links": {},
                }
            ),
            encoding="utf-8",
        )
        self.snapshot_path.write_text(json.dumps(self.snapshot()), encoding="utf-8")
        self.runs = []
        self.app = create_app(
            self.config_path, port=8765, no_refresh=True, runner=lambda: self.runs.append(1) or 0
        )
        self.app.testing = True
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
                    "links": [{"title": "Deck", "url": "https://example.com/deck.pptx"}],
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
                            "id": "p0207000-time_estimate",
                            "title": "Time estimate",
                            "stage": "Pre-Fabrication",
                            "due": "2026-11-02",
                            "status": "done",
                            "mark": "x",
                            "source": "tracker",
                        },
                    ],
                    "emails": [
                        {
                            "date": "2026-11-09T08:00:00-08:00",
                            "sender": "Diane Martin <diane@example.com>",
                            "subject": "Acme kickoff",
                            "trigger": "kickoff",
                            "reply": False,
                            "message_id": "msg-2",
                            "open": {"kind": "outlook", "entry_id": "entry-2"},
                        }
                    ],
                }
            ],
            "pending": [dict(PENDING)],
            "unmatched": [
                {
                    "date": "2026-11-09",
                    "subject": "Acme design review",
                    "reason": "no matching project, no trigger",
                    "message_id": "msg-3",
                    "keys": {"so": [], "qte": [], "project_no": []},
                    "open": {},
                },
                {
                    "date": "2026-11-09",
                    "subject": "Acme closeout",
                    "reason": "no trigger",
                    "message_id": "msg-4",
                    "keys": {"so": [], "qte": [], "project_no": []},
                    "open": {"kind": "eml", "path": str(self.eml_path)},
                },
            ],
        }

    def post(self, url: str, payload: dict, token: str | None = None, headers: dict | None = None):
        merged = dict(HOST)
        merged["X-Hub-Token"] = self.token if token is None else token
        merged["Origin"] = ORIGIN
        merged.update(headers or {})
        return self.client.post(url, json=payload, headers=merged)

    def test_host_header_rejected(self):
        response = self.client.get("/", headers={"Host": "evil.com"})
        self.assertEqual(response.status_code, 403)
        response = self.client.get("/", headers=HOST)
        self.assertEqual(response.status_code, 200)

    def test_pages_render(self):
        for url in ("/", "/monday", "/p/0207000"):
            response = self.client.get(url, headers=HOST)
            self.assertEqual(response.status_code, 200, url)
        self.assertEqual(self.client.get("/p/missing", headers=HOST).status_code, 404)

    def test_post_requires_token_and_origin(self):
        self.assertEqual(self.post("/api/rerun", {}, token="wrong").status_code, 403)
        self.assertEqual(
            self.post("/api/rerun", {}, headers={"Origin": "http://evil.com"}).status_code, 403
        )
        self.assertEqual(
            self.client.post("/api/rerun", data="x", headers=dict(HOST, **{"X-Hub-Token": self.token})).status_code,
            403,
        )
        response = self.post("/api/rerun", {})
        self.assertEqual(response.status_code, 200)

    def test_note_toggle_and_conflict(self):
        text, _nl, _bom, _enc = notes.read_text(self.note)
        lines = text.split("\n")
        index = next(i for i, line in enumerate(lines) if "review_cpr_so" in line)
        response = self.post(
            "/api/note/toggle",
            {"key": "0207000", "line_index": index, "expected_line": lines[index], "mark": "x"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("[x]", payload["line"])
        self.assertEqual(payload["hash"], sha256_bytes(self.note))
        self.assertIn("[x] Review CPR/SO", notes.read_text(self.note)[0])
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["tick_log"][-1]["task_id"], "p0207000-review_cpr_so")
        self.assertEqual(state["tick_log"][-1]["mark"], "x")

        stale = self.post(
            "/api/note/toggle",
            {"key": "0207000", "line_index": index, "expected_line": lines[index], "mark": "-"},
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.get_json()["error"], "conflict")

    def test_note_save_conflict_and_force(self):
        base = sha256_bytes(self.note)
        edited = notes.read_text(self.note)[0] + "\nmy own line\n"
        response = self.post("/api/note/save", {"key": "0207000", "text": edited, "base_hash": base})
        self.assertEqual(response.status_code, 200)
        self.assertIn("my own line", notes.read_text(self.note)[0])

        stale = self.post("/api/note/save", {"key": "0207000", "text": "gone", "base_hash": base})
        self.assertEqual(stale.status_code, 409)
        body = stale.get_json()
        self.assertEqual(body["error"], "conflict")
        self.assertIn("my own line", body["current_text"])
        self.assertEqual(body["current_hash"], sha256_bytes(self.note))

        forced = self.post(
            "/api/note/save",
            {"key": "0207000", "text": "forced text\n", "base_hash": base, "force": True},
        )
        self.assertEqual(forced.status_code, 200)
        self.assertEqual(notes.read_text(self.note)[0], "forced text\n")

    def test_due_override_set_and_clear(self):
        response = self.post(
            "/api/due_override",
            {"task_id": "p0207000-review_cpr_so", "due": "2026-11-20", "note": "blocked: parts"},
        )
        self.assertEqual(response.status_code, 200)
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["due_overrides"]["p0207000-review_cpr_so"]["due"], "2026-11-20")
        self.assertEqual(state["due_overrides"]["p0207000-review_cpr_so"]["note"], "blocked: parts")

        cleared = self.post("/api/due_override", {"task_id": "p0207000-review_cpr_so", "clear": True})
        self.assertEqual(cleared.status_code, 200)
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertNotIn("p0207000-review_cpr_so", state["due_overrides"])

        bad = self.post("/api/due_override", {"task_id": "p0207000-review_cpr_so", "due": "not-a-date"})
        self.assertEqual(bad.status_code, 400)

    def test_classify_sets_and_clears_override(self):
        response = self.post("/api/classify", {"message_id": "msg-3", "trigger": "design_review"})
        self.assertEqual(response.status_code, 200)
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["email_overrides"]["msg-3"], "design_review")

        cleared = self.post("/api/classify", {"message_id": "msg-3", "trigger": ""})
        self.assertEqual(cleared.status_code, 200)
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertNotIn("msg-3", state["email_overrides"])

        self.assertEqual(
            self.post("/api/classify", {"message_id": "msg-3", "trigger": "bogus"}).status_code, 400
        )

    def test_links_endpoint_cors(self):
        response = self.client.get(
            "/api/project/0207000/links",
            headers=dict(HOST, **{"Origin": "chrome-extension://abcdefghijklmnop"}),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("Access-Control-Allow-Origin"), "chrome-extension://abcdefghijklmnop"
        )
        payload = response.get_json()
        self.assertEqual(payload["title"], "Acme QTE-1")
        self.assertEqual(payload["hub_url"], "http://localhost:8765/p/0207000")
        urls = [link["url"] for link in payload["links"]]
        self.assertEqual(
            urls,
            ["https://example.com/sp", "https://example.com/deck.pptx"],
        )

        plain = self.client.get("/api/project/0207000/links", headers=dict(HOST, **{"Origin": ORIGIN}))
        self.assertIsNone(plain.headers.get("Access-Control-Allow-Origin"))

    def test_links_add_and_remove(self):
        added = self.post(
            "/api/links/add", {"key": "0207000", "title": "CPR", "url": "https://example.com/cpr"}
        )
        self.assertEqual(added.status_code, 200)
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["user_links"]["0207000"][0]["url"], "https://example.com/cpr")

        self.assertEqual(
            self.post("/api/links/add", {"key": "0207000", "title": "x", "url": "file:///c:/x"}).status_code,
            400,
        )

        removed = self.post("/api/links/remove", {"key": "0207000", "url": "https://example.com/cpr"})
        self.assertEqual(removed.status_code, 200)
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["user_links"]["0207000"], [])

    def test_pending_dismiss_and_preview(self):
        preview = self.client.get("/api/pending/preview?message_id=msg-1", headers=HOST)
        self.assertEqual(preview.status_code, 200)
        self.assertIn("Project Assignment - Acme", preview.get_json()["text"])

        dismissed = self.post("/api/pending/dismiss", {"message_id": "msg-1"})
        self.assertEqual(dismissed.status_code, 200)
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["pending"]["msg-1"]["status"], "dismissed")

    def test_add_project_task(self):
        response = self.post(
            "/api/tasks/add",
            {"key": "0207000", "description": "Buy parts", "due": "2026-11-20", "notes": "waiting"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["id"].startswith("m-"))
        self.assertEqual(payload["uid"], "0207000:" + payload["id"])
        lines = notes.read_text(self.note)[0].split("\n")
        heading = lines.index("**My tasks**")
        block = next(i for i, line in enumerate(lines) if line.strip().startswith("<!-- tracker:start"))
        self.assertLess(heading, block)
        self.assertIn(payload["id"], lines[heading + 1])
        self.assertIn("2026-11-20", lines[heading + 1])
        self.assertIn("waiting", lines[heading + 2])
        items = self.client.get("/api/tasks", headers=HOST).get_json()["tasks"]
        manual = [item for item in items if item["kind"] == "manual"]
        self.assertEqual(len(manual), 1)
        self.assertEqual(manual[0]["id"], payload["id"])
        self.assertEqual(manual[0]["stage"], "My tasks")
        self.assertEqual(manual[0]["due"], "2026-11-20")
        self.assertEqual(manual[0]["status"], "upcoming")

    def test_add_task_validation(self):
        self.assertEqual(self.post("/api/tasks/add", {"key": "nope", "description": "x"}).status_code, 404)
        self.assertEqual(self.post("/api/tasks/add", {"key": "0207000", "description": ""}).status_code, 400)
        self.assertEqual(
            self.post("/api/tasks/add", {"key": "0207000", "description": "x \U0001F4C5 2026-01-01"}).status_code,
            400,
        )
        self.assertEqual(
            self.post("/api/tasks/add", {"key": "0207000", "description": "x", "due": "nope"}).status_code,
            400,
        )

    def test_add_personal_task(self):
        personal = self.vault / "My Tasks.md"
        self.assertFalse(personal.is_file())
        response = self.post("/api/tasks/add", {"key": "", "description": "Renew licence", "due": ""})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(personal.is_file())
        text = notes.read_text(personal)[0]
        self.assertTrue(text.startswith("# My tasks\n\n"))
        self.assertIn("Renew licence", text)
        items = self.client.get("/api/tasks", headers=HOST).get_json()["tasks"]
        rows = [item for item in items if item["key"] == ""]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "manual")
        self.assertEqual(rows[0]["stage"], "Personal")
        self.assertIsNone(rows[0]["due"])
        self.assertEqual(rows[0]["status"], "open")

    def test_toggle_manual_by_id(self):
        added = self.post(
            "/api/tasks/add", {"key": "0207000", "description": "Buy parts", "due": "2026-11-20"}
        ).get_json()
        response = self.post("/api/task/toggle", {"key": "0207000", "id": added["id"], "mark": "x"})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["mark"], "x")
        self.assertIn("[x]", body["line"])
        self.assertIn("[x] Buy parts", notes.read_text(self.note)[0])
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["tick_log"][-1]["task_id"], added["id"])
        self.assertEqual(state["tick_log"][-1]["mark"], "x")
        self.assertEqual(state["tick_log"][-1]["key"], "0207000")

    def test_toggle_tracker_task_by_id_changes_one_char(self):
        before = notes.read_text(self.note)[0]
        response = self.post(
            "/api/task/toggle", {"key": "0207000", "id": "p0207000-review_cpr_so", "mark": "x"}
        )
        self.assertEqual(response.status_code, 200)
        after = notes.read_text(self.note)[0]
        self.assertEqual(len(before), len(after))
        diffs = [(old, new) for old, new in zip(before, after) if old != new]
        self.assertEqual(diffs, [(" ", "x")])
        self.assertIn("[x] Review CPR/SO", after)
        self.assertIn("[x] Time estimate", after)

    def test_toggle_by_index_conflict(self):
        lines = notes.read_text(self.note)[0].split("\n")
        index = next(i for i, line in enumerate(lines) if "review_cpr_so" in line)
        stale = self.post(
            "/api/task/toggle",
            {"key": "0207000", "line_index": index, "expected_line": "gone", "mark": "x"},
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.get_json()["error"], "conflict")
        ok = self.post(
            "/api/task/toggle",
            {"key": "0207000", "line_index": index, "expected_line": lines[index], "mark": "-"},
        )
        self.assertEqual(ok.status_code, 200)
        self.assertIn("[-] Review CPR/SO", notes.read_text(self.note)[0])

    def test_update_and_delete_manual(self):
        added = self.post(
            "/api/tasks/add",
            {"key": "0207000", "description": "Buy parts", "due": "2026-11-20", "notes": "waiting"},
        ).get_json()
        updated = self.post(
            "/api/tasks/update",
            {"key": "0207000", "id": added["id"], "description": "Buy more parts", "due": "", "notes": ""},
        )
        self.assertEqual(updated.status_code, 200)
        text = notes.read_text(self.note)[0]
        self.assertIn("Buy more parts", text)
        self.assertNotIn("2026-11-20", text)
        self.assertNotIn("waiting", text)
        deleted = self.post("/api/tasks/delete", {"key": "0207000", "id": added["id"]})
        self.assertEqual(deleted.status_code, 200)
        self.assertNotIn(added["id"], notes.read_text(self.note)[0])
        self.assertEqual(
            self.post("/api/tasks/delete", {"key": "0207000", "id": "p0207000-review_cpr_so"}).status_code, 400
        )
        self.assertEqual(
            self.post("/api/tasks/update", {"key": "0207000", "id": "p0207000-review_cpr_so"}).status_code, 400
        )

    def test_email_open_unknown(self):
        response = self.post("/api/email/open", {"message_id": "nope"})
        self.assertEqual(response.status_code, 404)
        self.assertIn("Refresh needed", response.get_json()["error"])

    def test_email_open_outlook(self):
        calls = []
        self.app.config["EMAIL_OPENER"] = lambda kind, target: calls.append((kind, target))
        response = self.post("/api/email/open", {"message_id": "msg-2"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls, [("outlook", "entry-2")])

    def test_email_open_eml(self):
        with mock.patch("os.startfile") as startfile:
            response = self.post("/api/email/open", {"message_id": "msg-4"})
        self.assertEqual(response.status_code, 200)
        startfile.assert_called_once_with(str(self.eml_path))

    def test_email_open_without_open_info(self):
        response = self.post("/api/email/open", {"message_id": "msg-3"})
        self.assertEqual(response.status_code, 404)
        self.assertIn("Refresh needed", response.get_json()["error"])

    def test_ui_layout(self):
        response = self.post("/api/ui", {"layout": "timeline"})
        self.assertEqual(response.status_code, 200)
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["ui"]["layout"], "timeline")
        self.assertEqual(self.post("/api/ui", {"layout": "bogus"}).status_code, 400)
        self.assertEqual(self.client.get("/?layout=focus", headers=HOST).status_code, 200)

    def test_gantt_endpoint(self):
        rows = self.client.get("/api/gantt", headers=HOST).get_json()
        self.assertTrue(rows)
        self.assertEqual(
            sorted(rows[0]), ["due", "key", "kind", "project", "start", "status", "title"]
        )
        for row in rows:
            self.assertLessEqual(row["start"], row["due"])

    def test_plotly_vendor(self):
        response = self.client.get("/vendor/plotly.min.js", headers=HOST)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/javascript")
        self.assertGreater(len(response.get_data()), 100000)
        self.assertEqual(response.headers.get("Cache-Control"), "max-age=86400")

    def test_pages_with_manual_tasks(self):
        due = (date.today() + timedelta(days=1)).isoformat()
        self.post("/api/tasks/add", {"key": "0207000", "description": "Buy parts", "due": due})
        self.post("/api/tasks/add", {"key": "", "description": "Renew licence", "due": due})
        for url in ("/", "/monday", "/p/0207000"):
            self.assertEqual(self.client.get(url, headers=HOST).status_code, 200, url)
        page = self.client.get("/p/0207000", headers=HOST).get_data(as_text=True)
        self.assertIn("Buy parts", page)
        monday = self.client.get("/monday", headers=HOST).get_data(as_text=True)
        self.assertIn("Renew licence", monday)
        self.assertIn("Buy parts", monday)


if __name__ == "__main__":
    unittest.main()
