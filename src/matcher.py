"""Bewertung (Matching) von Jobs gegen das Profil.

Das Scoring ist deterministisch und liegt zwischen 0 und 100. Keyword-Listen
und Gewichte stehen als Defaults hier im Code und können über config/search.yaml
überschrieben werden -> leicht erweiterbar und testbar.
"""
from __future__ import annotations

import re
from typing import Dict, List

from models import (
    Job,
    MatchResult,
    Profile,
    RECO_ABLEHNEN,
    RECO_GUT,
    RECO_PRUEFEN,
    RECO_SEHR_GUT,
)

# --- Default-Konfiguration (durch search.yaml überschreibbar) --------------
DEFAULT_BASE_SCORE = 50

DEFAULT_THRESHOLDS = {
    "sehr_gut": 85,
    "gut": 75,
    "pruefen": 60,
}

# Positive Keyword-Gruppen
JUNIOR_KEYWORDS = [
    "junior", "einsteiger", "berufseinsteiger", "trainee",
    "absolvent", "werkstudent", "praktikum", "ausbildung zum",
]
TECH_CORE_KEYWORDS = [
    "devops", "cloud", "kubernetes", "k8s", "docker", "aws",
    "ci/cd", "cicd", "terraform", "platform", "cloud-native",
    "cloud native", "helm", "container", "openshift", "gitops",
]
WEB_STACK_KEYWORDS = [
    "angular", "laravel", "fullstack", "full-stack", "full stack",
    "php", "typescript",
]
LOCATION_KEYWORDS = [
    "remote", "nrw", "nordrhein-westfalen", "deutschland", "homeoffice",
    "home office", "köln", "düsseldorf", "essen", "dortmund", "bonn",
    "duisburg", "aachen", "münster", "wuppertal", "hybrid",
]
CAREER_CHANGER_KEYWORDS = [
    "quereinsteiger", "quereinstieg", "quereinsteigerin",
    "praktische projekte", "auch ohne abschluss", "kein abschluss",
    "kein studium", "motivation zählt", "projekte statt zeugnisse",
]
EXTRAS_KEYWORDS = [
    "github", "gitlab", "portfolio", "linux", "automatisierung",
    "automation", "open source", "open-source", "skripting", "scripting",
]

# Negative Keyword-Gruppen
SENIOR_TITLE_KEYWORDS = ["senior", "lead", "principal", "architekt", "expert", "head of"]
WRONG_STACK_KEYWORDS = [
    "sap abap", "abap", "embedded c++", "embedded-entwicklung",
    "embedded entwicklung", "cobol", "mikrocontroller",
]
DEGREE_KEYWORDS = [
    "abgeschlossenes studium", "studium zwingend", "studium vorausgesetzt",
    "studienabschluss erforderlich", "abgeschlossenes hochschulstudium",
    "studium der informatik erforderlich",
]
TRAINING_KEYWORDS = ["abgeschlossene ausbildung", "abgeschlossene berufsausbildung"]
SUPPORT_KEYWORDS = [
    "1st level support", "first level support", "helpdesk", "service desk",
    "service-desk", "user-support", "anwenderbetreuung", "ticketbearbeitung",
]
NETWORK_KEYWORDS = [
    "netzwerkadministrator", "netzwerkadmin", "netzwerk-administrator",
    "cisco", "firewall-administration", "lan/wan",
]

# Punkte
POINTS = {
    "junior": 12,
    "tech_core_per_match": 5,
    "tech_core_max": 25,
    "web_stack": 6,
    "location": 8,
    "career_changer": 8,
    "extras": 6,
    "senior_required": 35,
    "experience_5plus": 30,
    "experience_3plus": 15,
    "degree_required": 20,
    "training_required": 12,
    "wrong_stack": 50,
    "support_role": 45,
    "network_only": 40,
}


def _matched(text: str, keywords: List[str]) -> List[str]:
    return [kw for kw in keywords if kw in text]


def _required_years(text: str) -> int:
    """Größte explizit geforderte Jahreszahl an Berufserfahrung."""
    years = [int(m) for m in re.findall(r"(\d+)\s*\+?\s*(?:jahre|jahren)", text)]
    return max(years) if years else 0


class Matcher:
    """Bewertet Jobs gegen ein Profil."""

    def __init__(self, profile: Profile, search_config: Dict | None = None):
        self.profile = profile
        cfg = search_config or {}
        self.base_score = cfg.get("scoring", {}).get("base_score", DEFAULT_BASE_SCORE)
        self.thresholds = {**DEFAULT_THRESHOLDS, **(cfg.get("thresholds") or {})}
        self.points = {**POINTS, **(cfg.get("points") or {})}

    # -- Hauptbewertung ----------------------------------------------------
    def score_job(self, job: Job) -> MatchResult:
        text = job.haystack
        title = job.title.lower()
        score = self.base_score
        positives: List[str] = []
        negatives: List[str] = []

        # --- Pluspunkte ---------------------------------------------------
        if _matched(title, JUNIOR_KEYWORDS) or _matched(text, JUNIOR_KEYWORDS):
            score += self.points["junior"]
            positives.append("Junior-/Einsteiger-Rolle passt zum Profil")

        core_hits = _matched(text, TECH_CORE_KEYWORDS)
        if core_hits:
            gain = min(
                len(core_hits) * self.points["tech_core_per_match"],
                self.points["tech_core_max"],
            )
            score += gain
            positives.append(
                "Kern-Tech passt (" + ", ".join(sorted(set(core_hits))) + ")"
            )

        web_hits = _matched(text, WEB_STACK_KEYWORDS)
        if web_hits:
            score += self.points["web_stack"]
            positives.append("Web-Stack passt (" + ", ".join(sorted(set(web_hits))) + ")")

        loc_hits = _matched(text, LOCATION_KEYWORDS)
        if loc_hits:
            score += self.points["location"]
            positives.append("Standort/Remote passt (" + ", ".join(sorted(set(loc_hits))) + ")")

        career_hits = _matched(text, CAREER_CHANGER_KEYWORDS)
        if career_hits:
            score += self.points["career_changer"]
            positives.append("Quereinsteiger / praktische Projekte willkommen")

        extra_hits = _matched(text, EXTRAS_KEYWORDS)
        if extra_hits:
            score += self.points["extras"]
            positives.append("Erwähnt " + ", ".join(sorted(set(extra_hits))))

        # --- Minuspunkte --------------------------------------------------
        is_junior_title = bool(_matched(title, JUNIOR_KEYWORDS))

        if not is_junior_title and _matched(title, SENIOR_TITLE_KEYWORDS):
            score -= self.points["senior_required"]
            negatives.append("Senior-/Lead-Rolle zwingend")

        years = _required_years(text)
        if years >= 5 or "5+" in text or "fünf jahre" in text:
            score -= self.points["experience_5plus"]
            negatives.append("Mindestens 5 Jahre Berufserfahrung gefordert")
        elif years >= 3 or "mehrjährige" in text or "langjährige" in text:
            score -= self.points["experience_3plus"]
            negatives.append("Mehrjährige Berufserfahrung gefordert")

        if _matched(text, DEGREE_KEYWORDS) and not career_hits:
            score -= self.points["degree_required"]
            negatives.append("Abgeschlossenes Studium zwingend gefordert")

        if _matched(text, TRAINING_KEYWORDS) and not career_hits:
            score -= self.points["training_required"]
            negatives.append("Abgeschlossene Ausbildung gefordert (keine Alternative genannt)")

        wrong_hits = _matched(text, WRONG_STACK_KEYWORDS)
        if wrong_hits:
            score -= self.points["wrong_stack"]
            negatives.append("Kompletter Fremd-Stack (" + ", ".join(sorted(set(wrong_hits))) + ")")

        # Support / Netzwerk nur abwerten, wenn KEIN Cloud/DevOps-Bezug da ist
        if _matched(text, SUPPORT_KEYWORDS) and not core_hits:
            score -= self.points["support_role"]
            negatives.append("Reine Support-/Helpdesk-Stelle")

        if _matched(text, NETWORK_KEYWORDS) and not core_hits:
            score -= self.points["network_only"]
            negatives.append("Reine Netzwerkadministration ohne Cloud/DevOps")

        # --- auf 0..100 begrenzen ----------------------------------------
        score = max(0, min(100, score))

        recommendation = self._recommend(score)
        skills = self._skills_to_emphasize(text)
        hint = self._cover_letter_hint(text, skills, bool(career_hits))

        return MatchResult(
            job=job,
            score=score,
            recommendation=recommendation,
            positive_reasons=positives,
            negative_reasons=negatives,
            skills_to_emphasize=skills,
            cover_letter_hint=hint,
        )

    def score_all(self, jobs: List[Job]) -> List[MatchResult]:
        return [self.score_job(j) for j in jobs]

    # -- Hilfsfunktionen ---------------------------------------------------
    def _recommend(self, score: int) -> str:
        if score >= self.thresholds["sehr_gut"]:
            return RECO_SEHR_GUT
        if score >= self.thresholds["gut"]:
            return RECO_GUT
        if score >= self.thresholds["pruefen"]:
            return RECO_PRUEFEN
        return RECO_ABLEHNEN

    def _skills_to_emphasize(self, text: str) -> List[str]:
        matched = [s for s in self.profile.skills if s.lower() in text]
        if not matched:
            # Fallback: die wichtigsten allgemeinen Skills
            matched = self.profile.skills[:5]
        return matched[:8]

    def _cover_letter_hint(self, text: str, skills: List[str], career: bool) -> str:
        parts: List[str] = []
        if skills:
            parts.append("Betone: " + ", ".join(skills[:4]) + ".")

        project = self._relevant_project(text)
        if project:
            parts.append(f"Verweise auf dein Projekt: {project}.")

        if career and self.profile.open_to_career_change:
            parts.append("Hebe deine praktischen Projekte als motivierter Quereinsteiger hervor.")

        return " ".join(parts)

    def _relevant_project(self, text: str) -> str:
        projects = self.profile.projects
        if not projects:
            return ""
        rules = [
            (("grafana", "prometheus", "loki", "tempo", "observability", "opentelemetry"),
             "observability"),
            (("angular", "laravel", "fullstack", "shop"), "angular"),
            (("kubernetes", "helm", "k3s", "docker", "pipeline"), "kubernetes"),
            (("agent", "multi-agent"), "agent"),
        ]
        for keywords, needle in rules:
            if any(k in text for k in keywords):
                for p in projects:
                    if needle in p.lower():
                        return p
        return projects[0]
