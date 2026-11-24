import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import yaml

from tracker import support_email as se
from tracker.hub.app import create_app

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 11, 24)
CPR = "https://hamiltonmarketing.sharepoint.com/:w:/s/RNOAPE/IQBx?e=x"
DR = "https://hamiltonmarketing.sharepoint.com/:p:/r/sites/RNOAPE/_layouts/15/Doc.aspx?sourcedoc=%7B8%7D&file=DesignReview-%5B0207774%5D-%5BMLI%5D_RevA.pptx&action=edit"

ROWS = [
    [None, "Bay Area", None, "South West"],
    ["PM", "Cindy", "Chris Derby", "Scott"],
    ["Sales", "Bronson Duck", "Jim Grieser", "Phil Darden"],
    ["ACE", "Grayson Kochi & Alex Wixtrom", "Ben McClain", "Aaron Danberry"],
    ["ACE Manager", "Dan Hamman", "Adan Orta", "Adan Orta"],
    ["Apps Manager", "Jon Engebretsen", "Jon Engebretsen", "Josh Brewer"],
    ["Apps Team Leads", "Joe Kennedy", "Sam Richards ", "Benny Grey"],
    ["L4 Sales and ACE", " Jose Ocampo & Benjamin Hur", " Jose Ocampo & Benjamin Hur", "Ryan Robbibaro"],
    [None, "CC Steve Kubala", "CC Steve Kubala", "CC Steve Kubala"],
]


def mli(**extra):
    row = {
        "key": "0207774",
        "name": "SO-296371",
        "customer": "Moses Lake Industries",
        "so": ["SO-296371"],
        "qte": [],
        "project_no": "0207774",
        "level": "L1",
        "cpr": CPR,
        "design_review": DR,
        "sheet": {
            "description": "Upgrade - MFX_Module_HPLCVialR",
            "pm_name": "Chris Derby",
            "me_name": "Harry Stewart",
            "sales_manager": "Jim Grieser",
        },
        "tasks": [],
    }
    row.update(extra)
    return row


class NameTests(unittest.TestCase):
    def test_split_people(self):
        self.assertEqual(se.split_people("Grayson Kochi & Alex Wixtrom"), ["Grayson Kochi", "Alex Wixtrom"])
        self.assertEqual(se.split_people("Samantha Scheid/Bulzomi"), ["Samantha Scheid|Samantha Bulzomi"])
        self.assertEqual(se.best_name("Samantha Scheid|Samantha Bulzomi", []), "Samantha Bulzomi")
        self.assertEqual(se.best_name("Samantha Scheid|Samantha Bulzomi", ["Samantha Scheid"]), "Samantha Scheid")
        self.assertEqual(se.split_people("Jason Patterson (AUS)"), ["Jason Patterson"])
        self.assertEqual(se.split_people("\xa0Steve Hofmann "), ["Steve Hofmann"])
        self.assertEqual(se.split_people("CC Steve Kubala"), ["Steve Kubala"])
        for empty in ("N/A", "NA", "Intern'tl", None):
            self.assertEqual(se.split_people(empty), [])

    def test_same_and_best_name(self):
        self.assertTrue(se.same_name("Phil Darden", "Phillip Darden"))
        self.assertFalse(se.same_name("Phil Darden", "Phil Dorsey"))
        self.assertEqual(se.split_people("None at this time"), [])
        known = ["Bernard Gray", "Phillip Darden", "Jonathon Anderson", "Samantha Bulzomi", "William Banks"]
        self.assertEqual(se.best_name("Benny Grey", known), "Bernard Gray")
        self.assertEqual(se.best_name("Bill Banks", known), "William Banks")
        self.assertEqual(se.best_name("Phil Darden", known), "Phillip Darden")
        self.assertEqual(se.best_name("Jon Anderson", known), "Jonathon Anderson")
        self.assertEqual(se.best_name("New Person", known), "New Person")


class L4Tests(unittest.TestCase):
    def test_l4_team_on_to_line_only_for_l4(self):
        teams = se.parse_territory(ROWS)
        l4 = se.build_draft(mli(level="L4"), teams, TODAY)
        self.assertEqual(l4["to"][-2:], ["Jose Ocampo", "Benjamin Hur"])
        self.assertNotIn("Jose Ocampo", l4["cc"])
        self.assertNotIn("Jose Ocampo", se.build_draft(mli(level="L2"), teams, TODAY)["to"])
        east = se.build_draft(mli(level="L4", sheet=dict(mli()["sheet"], sales_manager="Phil Darden")), teams, TODAY)
        self.assertIn("Ryan Robbibaro", east["to"])

    def test_l4_without_row_is_listed_as_missing(self):
        rows = [row for row in ROWS if row[0] != "L4 Sales and ACE"]
        draft = se.build_draft(mli(level="L4"), se.parse_territory(rows), TODAY)
        self.assertIn("L4 Sales Manager / ACE (Territory Teams)", draft["missing"])

    def test_l4_names_lead_the_top_line_as_mentions(self):
        draft = se.build_draft(mli(level="L4"), se.parse_territory(ROWS), TODAY)
        self.assertEqual(draft["mentions"]["@applicationsTeamLead"], ["Jose Ocampo", "Benjamin Hur", "Sam Richards"])
        out = se.fill_html("<p>@applicationsTeamLead @applicaitonsManager</p>", draft,
                           {"Jose Ocampo": "Jose.Ocampo@hamiltoncompany.com"})
        self.assertRegex(out, r'<a id="OWAAM[0-9A-F]{32}" href="mailto:Jose\.Ocampo@hamiltoncompany\.com"><span [^>]*>@Jose Ocampo</span></a> @Benjamin Hur @Sam Richards @Jon Engebretsen')


class TerritoryTests(unittest.TestCase):
    def test_parse_and_match_on_sales_manager(self):
        teams = se.parse_territory(ROWS)
        self.assertEqual(len(teams), 3)
        team = se.territory_for(teams, "Jim Grieser")
        self.assertEqual((team["ace"], team["apps_lead"], team["apps_manager"], team["cc"]),
                         (["Ben McClain"], ["Sam Richards"], ["Jon Engebretsen"], ["Steve Kubala"]))
        self.assertEqual(se.territory_for(teams, "Phillip Darden")["ace"], ["Aaron Danberry"])
        self.assertEqual(se.territory_for(teams, "Nobody Here"), {})

    def test_cell_with_cc_moves_that_person_to_cc(self):
        rows = [list(row) for row in ROWS]
        rows[6][1] = " Caitlin Costelle   (CC Josh Brewer)"
        team = se.parse_territory(rows)[0]
        self.assertEqual((team["apps_lead"], team["cc"]), (["Caitlin Costelle"], ["Steve Kubala", "Josh Brewer"]))

    def test_load_uses_cache_then_falls_back_when_download_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {"support_email": {"territory_cache": str(Path(tmp) / "t.json")}}

            def broken(url, target):
                raise OSError("offline")

            teams, problem = se.load_territory(cfg, downloader=broken)
            self.assertEqual(teams, [])
            self.assertIn("offline", problem)
            Path(tmp, "t.json").write_text(json.dumps({"teams": se.parse_territory(ROWS)}), encoding="utf-8")
            teams, problem = se.load_territory(cfg, downloader=broken)
            self.assertEqual((len(teams), problem), (3, ""))
            teams, problem = se.load_territory(cfg, fresh=True, downloader=broken)
            self.assertEqual(len(teams), 3)
            self.assertIn("using the last copy", problem)


class DraftTests(unittest.TestCase):
    def test_matches_the_sent_moses_lake_email(self):
        draft = se.build_draft(mli(), se.parse_territory(ROWS), TODAY)
        self.assertEqual(draft["subject"], "Applications Project Support - Moses Lake Industries | SO-296371 | 0207774 | Upgrade - MFX_Module_HPLCVialR")
        self.assertEqual(draft["to"], ["Ben McClain", "Sam Richards", "Jon Engebretsen"])
        self.assertEqual(draft["cc"], ["Chris Derby", "Harry Stewart", "Mark Cunningham", "Jim Grieser", "Steve Kubala"])
        self.assertEqual([d["label"] for d in draft["documents"]], ["CPR", "Design Review"])
        self.assertEqual(draft["documents"][1]["name"], "DesignReview-[0207774]-[MLI]_RevA.pptx")
        self.assertEqual(draft["replacements"]["addFromAboveSubject"], "Moses Lake Industries, SO-296371")
        self.assertEqual(draft["replacements"]["addOneWeekFromCurrentDate"], "12/01/2026")
        self.assertEqual(draft["missing"], ["Project page link", "Scheduled test date (from the design review)"])

    def test_missing_roles_and_links_are_listed(self):
        project = mli(cpr="", design_review="", dr_form="", level="L3")
        project["sheet"] = {"pm_name": "", "me_name": "", "sales_manager": "Nobody Here"}
        draft = se.build_draft(project, se.parse_territory(ROWS), TODAY)
        self.assertEqual(draft["to"], [])
        text = " | ".join(draft["missing"])
        for expected in ("Nobody Here", "PM", "ME", "CPR link", "Current DR link", "FAT link"):
            self.assertIn(expected, text)

    def test_previous_dr_used_when_no_current_dr(self):
        for extra in ({"previous_dr": [DR, CPR]}, {"dr_form": DR}):
            draft = se.build_draft(mli(design_review="", **extra), [], TODAY)
            self.assertEqual(draft["documents"][1]["url"], DR)
            self.assertIn("Current DR link", draft["missing"])

    def test_fill_html_inserts_documents_before_compliance_forms(self):
        li = "<li class=MsoListParagraph style='mso-list:l0 level1 lfo6'><span style='a'><span style='b'>"
        body = ("<p>@applicationsTeamLead @applicaitonsManager</p><p>updated on the project page. See the project page.</p>"
                "<p>by addOneWeekFromCurrentDate.</p>"
                "<p><b>Documents:</b></p><ul>%sPart Compliance Testing Forms:<o:p></o:p></span></span></li></ul>" % li)
        page = "https://hamiltonmarketing.sharepoint.com/sites/RNOAPE/0207774"
        out = se.fill_html(body, se.build_draft(mli(sharepoint=page), se.parse_territory(ROWS), TODAY))
        self.assertIn("@Sam Richards @Jon Engebretsen", out)
        self.assertIn("12/01/2026", out)
        self.assertIn('the <a href="%s">project page</a>. See the project page.' % page, out)
        self.assertLess(out.index("DesignReview-[0207774]"), out.index("Part Compliance Testing Forms:"))
        self.assertIn('%s<a href="%s">DesignReview-[0207774]-[MLI]_RevA.pptx</a><o:p></o:p></span></span></li>'
                      % (li, DR.replace("&", "&amp;")), out)
        self.assertNotIn("CPR: ", out)
        self.assertNotIn("Design Review: ", out)

    def test_project_page_missing_is_listed(self):
        self.assertIn("Project page link", se.build_draft(mli(), [], TODAY)["missing"])
        self.assertNotIn("Project page link", se.build_draft(mli(sharepoint="https://x"), [], TODAY)["missing"])


class HubTests(unittest.TestCase):
    def test_route_builds_draft_without_sending(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "territory.json"
            cache.write_text(json.dumps({"teams": se.parse_territory(ROWS)}), encoding="utf-8")
            (root / "snapshot.json").write_text(json.dumps({"projects": [mli()]}), encoding="utf-8")
            cfg = {
                "vault": str(root),
                "state_path": str(root / "state.json"),
                "snapshot_path": str(root / "snapshot.json"),
                "rules_path": str(ROOT / "tracker" / "rules.yaml"),
                "ape_sheet": {"enabled": False},
                "support_email": {"territory_cache": str(cache)},
            }
            path = root / "config.yaml"
            path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
            app = create_app(path, port=8765, no_refresh=True, runner=lambda: 0)
            app.config["TESTING"] = True
            opened = []
            app.config["DRAFT_OPENER"] = lambda conf, draft: opened.append(draft) or ["Sam Richards"]
            client = app.test_client()
            client.environ_base["HTTP_HOST"] = "127.0.0.1:8765"
            page = client.get("/p/0207774", base_url="http://127.0.0.1:8765")
            self.assertIn(b'data-action="support-email"', page.data)
            headers = {"X-Hub-Token": app.config["HUB_TOKEN"]}
            response = client.post("/api/project/support-email", json={"key": "0207774"}, headers=headers, base_url="http://127.0.0.1:8765")
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(opened[0]["to"], ["Ben McClain", "Sam Richards", "Jon Engebretsen"])
            self.assertIn("not found in Outlook: Sam Richards", response.get_json()["missing"])
            missing = client.post("/api/project/support-email", json={"key": "nope"}, headers=headers, base_url="http://127.0.0.1:8765")
            self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
