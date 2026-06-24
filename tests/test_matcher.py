"""Tests für den Matcher.

Ausführen:
    python -m unittest discover -s tests        (aus dem Projekt-Root)
    oder: pytest
"""
import sys
import unittest
from pathlib import Path

# src/ importierbar machen
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from matcher import Matcher  # noqa: E402
from models import (  # noqa: E402
    Job,
    Profile,
    RECO_ABLEHNEN,
    RECO_GUT,
    RECO_PRUEFEN,
    RECO_SEHR_GUT,
)


def make_profile() -> Profile:
    return Profile(
        name="Test Bewerber",
        email="test@example.de",
        target_roles=["Junior DevOps Engineer", "Junior Cloud Engineer"],
        skills=["Python", "Docker", "Kubernetes", "AWS", "Terraform",
                "CI/CD", "Linux", "Angular", "Laravel", "GitHub Actions"],
        projects=["AWS k3s Fullstack Deployment",
                  "Observability Stack mit Grafana, Loki, Tempo und OpenTelemetry"],
        locations=["Deutschland", "NRW", "Remote"],
        open_to_career_change=True,
    )


def job(jid="x", title="", company="ACME", location="", link="", description=""):
    return Job(id=jid, title=title, company=company, location=location,
               link=link, description=description)


class TestMatcher(unittest.TestCase):
    def setUp(self):
        self.matcher = Matcher(make_profile())

    def test_score_within_bounds(self):
        for j in [
            job(title="Senior", description="abap " * 50),
            job(title="Junior DevOps", description="docker kubernetes aws " * 50),
        ]:
            r = self.matcher.score_job(j)
            self.assertGreaterEqual(r.score, 0)
            self.assertLessEqual(r.score, 100)

    def test_strong_junior_devops_is_very_good(self):
        j = job(
            jid="1",
            title="Junior DevOps Engineer",
            location="Köln / Remote (NRW)",
            description=("Docker, Kubernetes, AWS, Terraform und CI/CD. Linux und "
                         "Automatisierung. Quereinsteiger mit GitHub Portfolio willkommen. "
                         "Remote in NRW."),
        )
        r = self.matcher.score_job(j)
        self.assertGreaterEqual(r.score, 85)
        self.assertEqual(r.recommendation, RECO_SEHR_GUT)
        self.assertIn("Docker", r.skills_to_emphasize)
        self.assertTrue(r.cover_letter_hint)

    def test_senior_with_degree_and_experience_is_rejected(self):
        j = job(
            title="Senior Cloud Architect",
            description=("5+ Jahre Erfahrung. Abgeschlossenes Studium der Informatik "
                         "zwingend. AWS, Kubernetes."),
        )
        r = self.matcher.score_job(j)
        self.assertLess(r.score, 60)
        self.assertEqual(r.recommendation, RECO_ABLEHNEN)
        self.assertTrue(any("Senior" in n for n in r.negative_reasons))

    def test_sap_abap_is_rejected(self):
        r = self.matcher.score_job(
            job(title="SAP ABAP Entwickler", description="Entwicklung in SAP ABAP.")
        )
        self.assertEqual(r.recommendation, RECO_ABLEHNEN)

    def test_embedded_cpp_is_rejected(self):
        r = self.matcher.score_job(
            job(title="Embedded C++ Entwickler",
                description="Embedded-Entwicklung in C++ fuer Mikrocontroller.")
        )
        self.assertEqual(r.recommendation, RECO_ABLEHNEN)

    def test_pure_helpdesk_is_rejected(self):
        r = self.matcher.score_job(
            job(title="1st Level IT Support",
                description="Helpdesk, Service Desk, Ticketbearbeitung, User-Support.")
        )
        self.assertEqual(r.recommendation, RECO_ABLEHNEN)

    def test_network_admin_without_cloud_is_rejected(self):
        r = self.matcher.score_job(
            job(title="Netzwerkadministrator",
                description="Cisco, Firewall-Administration, LAN/WAN.")
        )
        self.assertEqual(r.recommendation, RECO_ABLEHNEN)

    def test_career_changer_avoids_degree_penalty(self):
        with_alt = job(
            title="Junior DevOps Engineer",
            description=("Abgeschlossenes Studium von Vorteil, aber Quereinsteiger mit "
                         "praktischen Projekten willkommen. Docker, AWS, Linux."),
        )
        without_alt = job(
            title="Junior DevOps Engineer",
            description="Abgeschlossenes Studium zwingend. Docker, AWS, Linux.",
        )
        r_with = self.matcher.score_job(with_alt)
        r_without = self.matcher.score_job(without_alt)
        self.assertGreater(r_with.score, r_without.score)

    def test_experience_3_years_lowers_to_pruefen(self):
        j = job(
            title="DevOps Engineer",
            description=("Mindestens 3 Jahre Berufserfahrung mit Docker, Kubernetes, "
                         "AWS. Linux."),
        )
        r = self.matcher.score_job(j)
        self.assertEqual(r.recommendation, RECO_PRUEFEN)

    def test_recommendation_thresholds(self):
        m = self.matcher
        self.assertEqual(m._recommend(90), RECO_SEHR_GUT)
        self.assertEqual(m._recommend(85), RECO_SEHR_GUT)
        self.assertEqual(m._recommend(80), RECO_GUT)
        self.assertEqual(m._recommend(75), RECO_GUT)
        self.assertEqual(m._recommend(60), RECO_PRUEFEN)
        self.assertEqual(m._recommend(59), RECO_ABLEHNEN)
        self.assertEqual(m._recommend(0), RECO_ABLEHNEN)


if __name__ == "__main__":
    unittest.main()
