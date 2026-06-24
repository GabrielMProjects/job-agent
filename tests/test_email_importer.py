"""Tests für den Indeed-Alert-E-Mail-Import.

Es werden KEINE echten E-Mails / IMAP-Verbindungen / OpenAI-Calls verwendet.
Nur die reinen Funktionen (Parser, Dedup, Verarbeitung) werden getestet.

Ausführen:
    python -m unittest discover -s tests
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import email_importer as ei  # noqa: E402
from database import Database  # noqa: E402
from models import Profile  # noqa: E402

# Beispiel einer Indeed-Alert-Mail (vereinfacht). Job 1 und Job 3 sind identisch
# (Titel+Firma+Ort) -> müssen dedupliziert werden. Job 2 hat keine Beschreibung.
SAMPLE_HTML = """
<html><body>
  <a href="https://de.indeed.com/rc/clk?jk=aaa111&from=alert">Junior DevOps Engineer (m/w/d)</a>
  <span>CloudWerk GmbH</span>
  <span>Köln (Remote)</span>
  <span>Docker, Kubernetes, AWS, Terraform, CI/CD. Quereinsteiger willkommen.</span>
  <a href="https://de.indeed.com/rc/clk?jk=aaa111&from=alert">Job ansehen</a>

  <a href="https://de.indeed.com/rc/clk?jk=bbb222">Junior Cloud Engineer</a>
  <span>Nordlicht Systems</span>
  <span>Essen</span>

  <a href="https://de.indeed.com/rc/clk?jk=ccc333">Junior DevOps Engineer (m/w/d)</a>
  <span>CloudWerk GmbH</span>
  <span>Köln (Remote)</span>
  <span>Docker, Kubernetes, AWS, Terraform, CI/CD. Quereinsteiger willkommen.</span>

  <a href="https://de.indeed.com/account/alerts">Alle Jobs anzeigen</a>
</body></html>
"""


def make_profile() -> Profile:
    return Profile(
        name="Max Mustermann", email="example@example.com",
        skills=["Docker", "Kubernetes", "AWS", "Terraform", "CI/CD", "Linux", "Angular"],
        projects=["Cloud- & DevOps-Demoprojekt"],
        locations=["NRW", "Remote"], open_to_career_change=True,
    )


class _NoKeyTestCase(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.pop("OPENAI_API_KEY", None)

    def tearDown(self):
        if self._saved is not None:
            os.environ["OPENAI_API_KEY"] = self._saved


class TestParser(unittest.TestCase):
    def test_parses_three_entries(self):
        jobs = ei.parse_jobs_from_html(SAMPLE_HTML)
        self.assertEqual(len(jobs), 3)  # Dedup passiert erst in process_parsed_jobs

    def test_first_job_fields(self):
        jobs = ei.parse_jobs_from_html(SAMPLE_HTML)
        j = jobs[0]
        self.assertIn("Junior DevOps Engineer", j["title"])
        self.assertEqual(j["company"], "CloudWerk GmbH")
        self.assertTrue(j["location"].startswith("Köln"))
        self.assertIn("indeed.com", j["link"])
        self.assertIn("Docker", j["description"])

    def test_cta_anchor_is_not_a_job(self):
        # "Job ansehen" / "Alle Jobs anzeigen" dürfen keine eigenen Jobs werden
        titles = [j["title"] for j in ei.parse_jobs_from_html(SAMPLE_HTML)]
        self.assertNotIn("Job ansehen", titles)
        self.assertNotIn("Alle Jobs anzeigen", titles)

    def test_empty_html(self):
        self.assertEqual(ei.parse_jobs_from_html(""), [])


# Echtes Indeed-Format: Tracking-Link (cts.indeed.com) wird von Titel,
# "Job anzeigen" und "Mehr erfahren" geteilt; Firma + "PLZ Ort" als Textzeilen.
REAL_HTML = """
<html><body>
  <a href="https://cts.indeed.com/v3/TOKEN1">Job anzeigen</a>
  <a href="https://cts.indeed.com/v3/TOKEN1">Junior Cloud Engineer (m/w/d) - STACKIT</a>
  <a href="https://cts.indeed.com/v3/TOKEN1">Mehr erfahren</a>
  <span>Schwarz Digits KG</span>
  <span>74076 Heilbronn</span>
  <span>Anstellungsart</span><span>Vollzeit</span>
  <span>Stellenbeschreibung</span>
  <span>Wir suchen Cloud-Talente mit AWS, Kubernetes und Terraform.</span>
  <a href="https://cts.indeed.com/v3/FOOTER">Datenschutzerklärung</a>
  <a href="https://cts.indeed.com/v3/FOOTER2">Abbestellen</a>
</body></html>
"""


class TestRealIndeedFormat(unittest.TestCase):
    def test_single_job_with_tracking_links(self):
        jobs = ei.parse_jobs_from_html(REAL_HTML)
        self.assertEqual(len(jobs), 1)  # geteilter Link + Footer -> genau 1 Job
        j = jobs[0]
        self.assertIn("Junior Cloud Engineer", j["title"])
        self.assertEqual(j["company"], "Schwarz Digits KG")
        self.assertEqual(j["location"], "74076 Heilbronn")
        self.assertIn("cts.indeed.com", j["link"])
        self.assertIn("AWS", j["description"])


# Bestätigungs-/Verwaltungs-Mail OHNE echte Stelle (Fehlfälle aus der Praxis).
CONFIRM_HTML = """
<html><body>
  <a href="https://cts.indeed.com/v3/CONFIRM">Bestätigen</a>
  <span>© 2026 Indeed Deutschland GmbH, Hauptstr. 1, 10115 Berlin</span>
  <a href="https://cts.indeed.com/v3/MANAGE">Job-Benachrichtigungen verwalten</a>
  <a href="https://cts.indeed.com/v3/UNSUB">Abmelden</a>
  <a href="https://cts.indeed.com/v3/PRIV">Datenschutz</a>
  <a href="https://cts.indeed.com/v3/TOS">Nutzungsbedingungen</a>
  <a href="https://cts.indeed.com/v3/IMP">Impressum</a>
  <a href="https://cts.indeed.com/v3/SET">E-Mail-Einstellungen</a>
</body></html>
"""

# Echte Stelle + dieselben Footer-/Verwaltungs-Links gemischt.
MIXED_HTML = REAL_HTML.replace("</body></html>", "") + """
  <a href="https://cts.indeed.com/v3/CONFIRM">Bestätigen</a>
  <a href="https://cts.indeed.com/v3/MANAGE">Job-Benachrichtigungen verwalten</a>
  <span>© 2026 Indeed Deutschland GmbH</span>
</body></html>
"""


class TestFooterFiltering(unittest.TestCase):
    def test_confirmation_only_mail_yields_no_jobs(self):
        self.assertEqual(ei.parse_jobs_from_html(CONFIRM_HTML), [])

    def test_no_footer_titles_extracted(self):
        titles = " | ".join(j["title"] for j in ei.parse_jobs_from_html(CONFIRM_HTML))
        for bad in ("Bestätigen", "verwalten", "Indeed Deutschland", "Impressum"):
            self.assertNotIn(bad, titles)

    def test_mixed_mail_keeps_only_real_job(self):
        jobs = ei.parse_jobs_from_html(MIXED_HTML)
        self.assertEqual(len(jobs), 1)
        self.assertIn("Junior Cloud Engineer", jobs[0]["title"])

    def test_confirmation_mail_sends_no_telegram(self):
        sent = []

        class FakeNotifier:
            def notify_job(self, result, analysis=None):
                sent.append(result.job.id)

        from database import Database
        db = Database(":memory:")
        parsed = ei.parse_jobs_from_html(CONFIRM_HTML)
        ei.process_parsed_jobs(parsed, db, make_profile(), {}, ei.EmailConfig(),
                               notifier=FakeNotifier(), dry_run=False)
        self.assertEqual(sent, [])           # keine Benachrichtigung
        self.assertEqual(len(db.get_all()), 0)  # nichts gespeichert
        db.close()


# Digest-Format mit Firmen-Sternebewertung zwischen Firma und Ort.
RATING_HTML = """
<html><body>
  <a href="https://cts.indeed.com/v3/T1">Platform Engineer (m/w/d)</a>
  <span>Hays Professional Solutions GmbH</span>
  <span>4.3</span>
  <span>Köln</span>

  <a href="https://cts.indeed.com/v3/T2">Software Engineer (all genders)</a>
  <span>(3,5)</span>
  <span>Home Office</span>
</body></html>
"""


class TestRatingFilter(unittest.TestCase):
    def test_rating_not_in_company_or_location(self):
        jobs = ei.parse_jobs_from_html(RATING_HTML)
        self.assertEqual(len(jobs), 2)
        j1 = jobs[0]
        self.assertIn("Hays", j1["company"])
        self.assertEqual(j1["location"], "Köln")
        # keine Bewertungszahl in Firma/Ort
        for j in jobs:
            self.assertNotIn("4.3", j["company"] + j["location"])
            self.assertNotIn("3,5", j["company"] + j["location"])

    def test_looks_like_rating_helper(self):
        for good in ("4.3", "3,5", "(3.5)", "★ 4,3", "5", "0"):
            self.assertTrue(ei._looks_like_rating(good), good)
        for bad in ("Köln", "GmbH", "55.000 €", "10115 Berlin", "Hays 4.3"):
            self.assertFalse(ei._looks_like_rating(bad), bad)


class TestDedup(unittest.TestCase):
    def test_same_identity_same_key_and_id(self):
        a = {"title": "X", "company": "Y", "location": "Z", "link": "l1"}
        b = {"title": "x", "company": "y ", "location": " Z", "link": "l2-other"}
        self.assertEqual(ei.dedupe_key(a), ei.dedupe_key(b))
        self.assertEqual(ei.make_job_id(a), ei.make_job_id(b))

    def test_fallback_to_link_without_identity(self):
        a = {"title": "", "company": "", "location": "", "link": "https://x/y"}
        self.assertEqual(ei.dedupe_key(a), "https://x/y")


class TestProcess(_NoKeyTestCase):
    def test_dedupe_in_process(self):
        parsed = ei.parse_jobs_from_html(SAMPLE_HTML)
        summaries = ei.process_parsed_jobs(
            parsed, None, make_profile(), {}, ei.EmailConfig(), dry_run=True)
        self.assertEqual(len(summaries), 2)  # 3 geparst -> 2 eindeutig

    def test_dry_run_saves_nothing(self):
        db = Database(":memory:")
        parsed = ei.parse_jobs_from_html(SAMPLE_HTML)
        ei.process_parsed_jobs(parsed, db, make_profile(), {}, ei.EmailConfig(), dry_run=True)
        self.assertEqual(len(db.get_all()), 0)
        db.close()

    def test_non_dry_run_saves_and_no_openai(self):
        db = Database(":memory:")
        parsed = ei.parse_jobs_from_html(SAMPLE_HTML)
        summaries = ei.process_parsed_jobs(
            parsed, db, make_profile(), {}, ei.EmailConfig(),
            notifier=None, jobs_csv=None, dry_run=False)
        self.assertEqual(len(db.get_all()), 2)
        self.assertTrue(all(not s["analyzed"] for s in summaries))   # kein Key -> keine KI
        self.assertTrue(all(not s["notified"] for s in summaries))   # kein Notifier
        db.close()

    def test_short_description_is_marked(self):
        parsed = ei.parse_jobs_from_html(SAMPLE_HTML)
        summaries = ei.process_parsed_jobs(
            parsed, None, make_profile(), {}, ei.EmailConfig(), dry_run=True)
        cloud = [s for s in summaries if "Cloud Engineer" in s["title"]][0]
        self.assertTrue(cloud["from_alert"])

    def test_telegram_only_above_threshold(self):
        # Notifier-Ersatz, der nur zählt (kein Netzwerk)
        sent = []

        class FakeNotifier:
            def notify_job(self, result, analysis=None):
                sent.append(result.score)

        db = Database(":memory:")
        parsed = ei.parse_jobs_from_html(SAMPLE_HTML)
        cfg = ei.EmailConfig(min_score_telegram=75)
        ei.process_parsed_jobs(parsed, db, make_profile(), {}, cfg,
                               notifier=FakeNotifier(), dry_run=False)
        self.assertTrue(all(score >= 75 for score in sent))
        db.close()


class TestCsvAppend(unittest.TestCase):
    def test_append_and_dedupe_by_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jobs.csv"
            rows = [
                {"id": "em-1", "title": "A", "company": "C", "location": "L",
                 "link": "x", "description": "d"},
                {"id": "em-2", "title": "B", "company": "C", "location": "L",
                 "link": "y", "description": "d"},
            ]
            self.assertEqual(ei.append_jobs_to_csv(path, rows), 2)
            # zweiter Lauf mit einem schon vorhandenen + einem neuen
            rows2 = rows + [{"id": "em-3", "title": "C2", "company": "C",
                             "location": "L", "link": "z", "description": "d"}]
            self.assertEqual(ei.append_jobs_to_csv(path, rows2), 1)


class TestConfig(unittest.TestCase):
    def test_infer_host(self):
        self.assertEqual(ei.infer_imap_host("a@gmail.com"), "imap.gmail.com")
        self.assertEqual(ei.infer_imap_host("a@outlook.de"), "outlook.office365.com")
        self.assertEqual(ei.infer_imap_host("a@example.org"), "imap.example.org")

    def test_not_configured_by_default(self):
        self.assertFalse(ei.EmailConfig().is_configured())


if __name__ == "__main__":
    unittest.main()
