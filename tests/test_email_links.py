from __future__ import annotations

import unittest
from datetime import datetime, timezone

from tracker import extract, notes
from tracker.models import Email
from tracker.tracker import project_info

SP = "https://hamiltonmarketing.sharepoint.com"
CPR_DOC = SP + "/:w:/s/RNOAPE/IQB9LhRchLlARK?e=itYdQp"
DR_DECK = SP + "/:p:/r/sites/RNOAPE/_layouts/15/Doc.aspx?sourcedoc=%7BA1%7D&file=DesignReview-0207757-LILLY_RevA.pptx&action=edit"
OLD_DR = SP + "/sites/RNOAPE/Project%20Database/Neo/02%20-%20Mechanical%20Design%20Documents/DesignReview_0205423_Exact_RevA.5.0.pptx"
ZIP = SP + "/sites/RNOAPE/Project%20Database/DesiCorp/HAM.2POS.MD_0834_open.zip"


def email(subject: str, body: str, day: int, message_id: str = "", to: str = "", cc: str = "") -> Email:
    return Email(
        message_id=message_id or subject + str(day),
        subject=subject,
        sender="pm@hamiltoncompany.com",
        to=to,
        cc=cc,
        date=datetime(2026, 11, day, 9, tzinfo=timezone.utc),
        body_text=body,
    )


class KickoffLinkTests(unittest.TestCase):
    def test_dr_form_and_cpr_kept_apart(self):
        body = "Project page here <%s/page>\nCPR form here <%s>\nDR form here <%s>\n" % (SP, CPR_DOC, DR_DECK)
        parsed = extract.parse_kickoff_body(body)
        self.assertEqual((parsed["cpr"], parsed["dr_form"]), (CPR_DOC, DR_DECK))

    def test_cpr_line_holding_a_previous_dr_becomes_dr_form(self):
        for line in ("CPR form  Previous DR <%s>" % OLD_DR, "CPR form here <%s>" % DR_DECK):
            parsed = extract.parse_kickoff_body(line)
            self.assertEqual(parsed["cpr"], "", line)
            self.assertIn("DesignReview", parsed["dr_form"], line)

    def test_dr_mentioned_before_the_link_is_not_a_dr_form(self):
        parsed = extract.parse_kickoff_body("CPR form here <%s> (replace with DR link for repeat HW designs)" % CPR_DOC)
        self.assertEqual((parsed["cpr"], parsed["dr_form"]), (CPR_DOC, ""))


class DesignReviewEmailTests(unittest.TestCase):
    def test_picks_the_deck_not_the_signature_or_attachments(self):
        body = (
            "hamiltoncompany.com <https://www.hamiltoncompany.com/>\n"
            "HAM.2POS.MD_0834_open.zip <%s>\n"
            "Please review this Design Review for MLI <%s>\n" % (ZIP, DR_DECK)
        )
        self.assertEqual(extract.design_review_link(body), DR_DECK)

    def test_no_slides_means_no_link(self):
        self.assertEqual(extract.design_review_link("See <https://www.hamiltoncompany.com/>"), "")


def deck(name: str) -> str:
    return SP + "/:p:/r/sites/RNOAPE/_layouts/15/Doc.aspx?sourcedoc=%7BA1%7D&file=" + name + "&action=edit"


REV_A = deck("DesignReview-%5B0207612%5D-%5BBayer%5D_16SEP26_RevA.pptx")
REV_B = deck("DesignReview-%5B0207612%5D-%5BBayer%5D_13NOV26_RevB.pptx")
OTHER = deck("DesignReview_0207298_Genentech_23DEC2024_RevA.4.0.pptx")


class ProjectInfoTests(unittest.TestCase):
    def info(self, matched=None, related=None, number="0207757"):
        return project_info(matched or {}, related or {}, {"SO-1": {number}})["SO-1"]

    def test_current_and_previous_dr_and_order(self):
        ko = email("Acme SO-1 Kickoff Notification", "CPR form here <%s>\nDR form here <%s>" % (CPR_DOC, OLD_DR), 1)
        original = email("Design Review_Acme_SO-1", "Please review <%s>" % DR_DECK, 5)
        info = self.info({"SO-1": [(ko, "kickoff", False), (original, "design_review", False)]})
        self.assertEqual((info["cpr"], info["design_review"], info["previous_dr"]), (CPR_DOC, DR_DECK, [OLD_DR]))
        titles = [link["title"] for link in info["links"]]
        self.assertEqual(titles[:3], ["CPR", "Current DR", "Previous DR"])

    def test_found_in_a_reply_or_a_dr_subject_without_trigger(self):
        reply = email("RE: Acme SO-1 Kickoff Notification", "Here it is <%s>" % DR_DECK, 9)
        self.assertEqual(self.info(related={"SO-1": [reply]})["design_review"], DR_DECK)
        short = SP + "/:p:/s/RNOAPE/IQBshort?e=x"
        peer = email("Acme | SO-1 | DR Peer Review", "Deck <%s>" % short, 9)
        internal = email("Internal Review_DR_Acme_SO-1", "Deck <%s>" % short, 9)
        for item in (peer, internal):
            self.assertEqual(self.info(related={"SO-1": [item]})["design_review"], short)
        other = email("RE: Project Assignment - Acme | SO-1", "See <%s>" % short, 9)
        self.assertEqual(self.info(related={"SO-1": [other]})["design_review"], "")

    def test_short_link_labelled_design_review_counts(self):
        short = SP + "/:p:/s/RNOAPE/IQAIF?e=x"
        mail = email("A&T Notification - SO-1 - Acme", "Design Review <%s>" % short, 9)
        self.assertEqual(self.info(related={"SO-1": [mail]})["design_review"], short)

    def test_number_in_the_email_subject_counts_as_this_project(self):
        mail = email("0207840 - Exact - SO-1", "Deck <%s>" % deck("DesignReview+-+0207840+-+Exact_28OCT26_RevA.pptx"), 9)
        self.assertIn("0207840", self.info(related={"SO-1": [mail]}, number="0206618")["design_review"])

    def test_other_project_decks_become_previous_dr(self):
        mail = email("RE: Acme SO-1 Kickoff Notification", "Old one <%s>\nThis one <%s>" % (OTHER, REV_A), 9)
        info = self.info(related={"SO-1": [mail]}, number="0207612")
        self.assertEqual((info["design_review"], info["previous_dr"]), (REV_A, [OTHER]))

    def test_newer_revision_replaces_older_and_unverified_is_rejected(self):
        first = email("Bayer | SO-1 | DR Review", "<%s>" % REV_A, 3)
        second = email("RE: Bayer | SO-1 | Cell culture", "<%s>" % REV_B, 9)
        info = self.info(related={"SO-1": [second, first]}, number="0207612")
        self.assertEqual(info["design_review"], REV_B)
        later_older = email("RE: Bayer | SO-1", "<%s>" % REV_A, 12)
        no_rev = email("RE: Bayer | SO-1", "<%s>" % deck("DesignReview-0207612-Bayer.pptx"), 13)
        info = self.info(related={"SO-1": [first, second, later_older, no_rev]}, number="0207612")
        self.assertEqual(info["design_review"], REV_B)

    def test_support_emails_are_ignored(self):
        mail = email("Applications Project Support - Acme | SO-1 | 0207757", "Design Review: <%s>" % DR_DECK, 9)
        reply = email("RE: Applications Project Support - Acme | SO-1", "<%s>" % DR_DECK, 10)
        self.assertEqual(self.info(related={"SO-1": [mail, reply]})["design_review"], "")


class RevisionTests(unittest.TestCase):
    def test_revisions(self):
        self.assertEqual(extract.deck_revision("DesignReview_0206860_Vertex_21AUG2023_RevB.4.0.pptx"), ("B", (4, 0)))
        self.assertEqual(extract.deck_revision("DesignReview_0206843_SEEGENE_18072023_RevA .7.0.pptx"), ("A", (7, 0)))
        self.assertIsNone(extract.deck_revision("DR-0207353-001-APE-6186-PFIZER.pptx"))
        self.assertTrue(extract.is_newer_revision("x_RevB.pptx", "x_RevA.pptx"))
        self.assertTrue(extract.is_newer_revision("x_RevB.4.0.pptx", "x_RevB.3.0.pptx"))
        self.assertFalse(extract.is_newer_revision("x_RevA.pptx", "x_RevB.pptx"))
        self.assertFalse(extract.is_newer_revision("x.pptx", "x_RevA.pptx"))
        self.assertTrue(extract.is_design_review_deck("DR-0207353-001-APE-6186-PFIZER.pptx"))
        self.assertFalse(extract.is_design_review_deck("Standard Presto Integrations.pptx"))


class FrontmatterTests(unittest.TestCase):
    def test_current_dr_replaced_only_by_newer_revision(self):
        merged = notes.merge_frontmatter({"design_review": REV_A}, {"design_review": REV_B})
        self.assertEqual(merged["design_review"], REV_B)
        for existing in (REV_B, "https://mine"):
            merged = notes.merge_frontmatter({"design_review": existing}, {"design_review": REV_A})
            self.assertEqual(merged["design_review"], existing)
        self.assertEqual(notes.merge_frontmatter({}, {"design_review": REV_A})["design_review"], REV_A)

    def test_dr_form_moves_to_previous_dr_and_list_grows(self):
        merged = notes.merge_frontmatter({"dr_form": OLD_DR}, {"previous_dr": [OTHER, OLD_DR]})
        self.assertNotIn("dr_form", merged)
        self.assertEqual(merged["previous_dr"], [OLD_DR, OTHER])

    def test_previous_dr_drops_the_current_deck(self):
        same_file = REV_A.replace("&action=edit", "")
        merged = notes.merge_frontmatter({"design_review": REV_A, "dr_form": same_file}, {"previous_dr": [OTHER]})
        self.assertEqual(merged["previous_dr"], [OTHER])


if __name__ == "__main__":
    unittest.main()
