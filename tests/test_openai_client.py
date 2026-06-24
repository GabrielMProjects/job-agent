"""Tests für die OpenAI-Integration – OHNE echte API-Calls.

- Fallback-Tests erzwingen, dass KEIN OPENAI_API_KEY gesetzt ist.
- "KI funktioniert"-Tests mocken `is_available` und `_chat`, es geht also nie
  eine echte Anfrage raus.

Ausführen:
    python -m unittest discover -s tests
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import application_generator as ag  # noqa: E402
import config_loader  # noqa: E402
import openai_client  # noqa: E402
from models import Job, JobAnalysis, MatchResult, Profile  # noqa: E402


def make_profile() -> Profile:
    return Profile(name="Test Bewerber", email="test@example.de",
                   skills=["Docker", "Kubernetes", "AWS"],
                   projects=["AWS k3s Fullstack Deployment"],
                   open_to_career_change=True)


def make_result() -> MatchResult:
    return MatchResult(
        job=Job(id="1", title="Junior DevOps Engineer", company="CloudWerk",
                location="Köln / Remote", link="https://example.com/1",
                description="Docker, Kubernetes, AWS"),
        score=92, recommendation="Sehr gut",
        positive_reasons=["Kern-Tech passt"], negative_reasons=[],
        skills_to_emphasize=["Docker", "Kubernetes", "AWS"],
        cover_letter_hint="Betone: Docker, Kubernetes, AWS.",
    )


class _NoKeyTestCase(unittest.TestCase):
    """Entfernt OPENAI_API_KEY für die Dauer des Tests."""

    def setUp(self):
        self._saved = os.environ.pop("OPENAI_API_KEY", None)
        self._saved_model = os.environ.pop("OPENAI_MODEL", None)

    def tearDown(self):
        if self._saved is not None:
            os.environ["OPENAI_API_KEY"] = self._saved
        if self._saved_model is not None:
            os.environ["OPENAI_MODEL"] = self._saved_model


# --- Fallback ohne Key -----------------------------------------------------
class TestAvailability(_NoKeyTestCase):
    def test_no_key_means_unavailable(self):
        self.assertFalse(openai_client.has_api_key())
        self.assertFalse(openai_client.is_available())

    def test_default_model(self):
        self.assertEqual(openai_client.get_model(), "gpt-5.5-mini")

    def test_analyze_job_raises_without_key(self):
        with self.assertRaises(RuntimeError):
            openai_client.analyze_job(make_result().job, "cv", "zeugnis")

    def test_cover_letter_raises_without_key(self):
        with self.assertRaises(RuntimeError):
            openai_client.generate_cover_letter_with_ai(
                make_result().job, make_profile(), make_result())


class TestGeneratorFallback(_NoKeyTestCase):
    def test_generate_draft_uses_local_without_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, used_ai = ag.generate_draft_ex(make_result(), make_profile(), Path(tmp))
            self.assertFalse(used_ai)
            text = path.read_text(encoding="utf-8")
            self.assertIn("lokalem Generator", text)
            self.assertIn("Sehr geehrte Damen und Herren", text)
            self.assertIn("NICHT automatisch versenden", text)


# --- CV/Zeugnis als Kontext (lokaler Loader) -------------------------------
class TestContextLoading(unittest.TestCase):
    def test_missing_file_returns_empty(self):
        self.assertEqual(config_loader.load_text(Path("gibt-es-nicht-xyz.md")), "")

    def test_reads_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "cv.md"
            p.write_text("Mein echter Lebenslauf", encoding="utf-8")
            self.assertIn("Lebenslauf", config_loader.load_text(p))

    def test_profile_local_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "profile.yaml"
            base.write_text(
                "name: Platzhalter\ncontact:\n  email: x@example.com\n"
                "skills:\n  - Docker\n", encoding="utf-8")
            # ohne lokales Override -> Platzhalter
            p = config_loader.load_profile(base)
            self.assertEqual(p.name, "Platzhalter")
            # mit lokalem Override -> echte Werte gewinnen, Rest bleibt
            (Path(tmp) / "profile.local.yaml").write_text(
                "name: Echter Name\ncontact:\n  email: echt@local.test\n", encoding="utf-8")
            p2 = config_loader.load_profile(base)
            self.assertEqual(p2.name, "Echter Name")
            self.assertEqual(p2.email, "echt@local.test")
            self.assertIn("Docker", p2.skills)  # nicht überschriebene Felder bleiben

    def test_local_analysis_from_match(self):
        a = JobAnalysis.from_local(make_result())
        self.assertTrue(a.fits)
        self.assertEqual(a.verdict, "passt")
        self.assertEqual(a.source, "lokal")
        self.assertIn("Docker", a.key_skills)


# --- KI funktioniert (gemockt, kein Netzwerk) ------------------------------
class TestPdf(_NoKeyTestCase):
    def test_pdf_safe_is_latin1(self):
        s = ag._pdf_safe("Cloud – Engineer „hallo“ … 5€")
        s.encode("latin-1")  # darf NICHT werfen
        self.assertNotIn("–", s)

    def test_package_includes_pdf_if_fpdf(self):
        try:
            import fpdf  # noqa: F401
        except Exception:
            self.skipTest("fpdf2 nicht installiert")
        with tempfile.TemporaryDirectory() as tmp:
            pkg, used_ai, files = ag.create_application_package(
                make_result(), make_profile(), Path(tmp), cv_text="cv", zeugnis_text="z")
            self.assertIn("Anschreiben.pdf", files)
            self.assertTrue((pkg / "Anschreiben.pdf").exists())
            self.assertGreater((pkg / "Anschreiben.pdf").stat().st_size, 200)


class TestWithMockedAI(unittest.TestCase):
    def test_analyze_job_parses_json(self):
        canned = ('{"fits": true, "reasons": ["passt gut", "DevOps-Fokus"], '
                  '"key_skills": ["Docker", "AWS"], "recommendation": "Bewirb dich."}')
        with mock.patch.object(openai_client, "is_available", return_value=True), \
             mock.patch.object(openai_client, "_chat", return_value=canned):
            a = openai_client.analyze_job(make_result().job, "CV", "ZEUGNIS", 92, "Sehr gut")
        self.assertTrue(a.fits)
        self.assertEqual(a.verdict, "passt")
        self.assertIn("Docker", a.key_skills)
        self.assertLessEqual(len(a.reasons), 5)
        self.assertEqual(a.source, "openai")

    def test_analyze_job_handles_non_json(self):
        with mock.patch.object(openai_client, "is_available", return_value=True), \
             mock.patch.object(openai_client, "_chat",
                               return_value="Der Bewerber passt gut zur Stelle."):
            a = openai_client.analyze_job(make_result().job, "", "")
        self.assertTrue(a.fits)          # "passt" ohne "passt nicht"
        self.assertEqual(a.source, "openai")

    def test_cv_and_zeugnis_land_im_prompt(self):
        captured = {}

        def fake_chat(system, user):
            captured["user"] = user
            return "Sehr geehrte Damen und Herren, ... Mit freundlichen Grüßen"

        with mock.patch.object(openai_client, "is_available", return_value=True), \
             mock.patch.object(openai_client, "_chat", side_effect=fake_chat):
            txt = openai_client.generate_cover_letter_with_ai(
                make_result().job, make_profile(), make_result(),
                cv_text="MEIN_CV_MARKER", zeugnis_text="MEIN_ZEUGNIS_MARKER")
        self.assertIn("MEIN_CV_MARKER", captured["user"])
        self.assertIn("MEIN_ZEUGNIS_MARKER", captured["user"])
        self.assertIn("freundlichen Grüßen", txt)

    def test_generate_draft_uses_ai_when_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(openai_client, "is_available", return_value=True), \
                 mock.patch.object(openai_client, "_chat",
                                   return_value="Sehr geehrte Damen und Herren, Test. "
                                                "Mit freundlichen Grüßen"):
                path, used_ai = ag.generate_draft_ex(
                    make_result(), make_profile(), Path(tmp),
                    cv_text="CV", zeugnis_text="ZEUGNIS")
            self.assertTrue(used_ai)
            self.assertIn("KI (OpenAI", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
