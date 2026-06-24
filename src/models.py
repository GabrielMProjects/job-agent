"""Datenmodelle für den Job-Agent.

Reine Standard-Library (dataclasses). Diese Strukturen werden in allen
Modulen genutzt, damit Matcher, Datenbank und Telegram-Bot mit den
gleichen Objekten arbeiten.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


# --- Status-Werte (lokale Bewerbungs-Pipeline) ---------------------------
# Diese Strings werden 1:1 in der SQLite-Datenbank gespeichert.
STATUS_NEW = "neu"            # noch nicht bearbeitet
STATUS_APPLY = "bewerben"     # zur Bewerbung markiert
STATUS_APPLIED = "beworben"   # Bewerbung wurde (manuell) verschickt
STATUS_REJECTED = "abgelehnt"  # nicht interessant
STATUS_LATER = "später"       # später ansehen

ALL_STATUSES = [
    STATUS_NEW,
    STATUS_APPLY,
    STATUS_APPLIED,
    STATUS_LATER,
    STATUS_REJECTED,
]


# --- Empfehlungs-Stufen ---------------------------------------------------
RECO_SEHR_GUT = "Sehr gut"
RECO_GUT = "Gut"
RECO_PRUEFEN = "Prüfen"
RECO_ABLEHNEN = "Ablehnen"


@dataclass
class Job:
    """Ein einzelner Job aus data/jobs.csv."""

    id: str
    title: str
    company: str
    location: str
    link: str
    description: str

    @property
    def haystack(self) -> str:
        """Gesamter durchsuchbarer Text (klein geschrieben) für das Matching."""
        return " ".join(
            [self.title, self.company, self.location, self.description]
        ).lower()


@dataclass
class MatchResult:
    """Das Ergebnis der Bewertung eines Jobs."""

    job: Job
    score: int
    recommendation: str
    positive_reasons: List[str] = field(default_factory=list)
    negative_reasons: List[str] = field(default_factory=list)
    skills_to_emphasize: List[str] = field(default_factory=list)
    cover_letter_hint: str = ""


@dataclass
class JobAnalysis:
    """Ergebnis der (KI- oder lokalen) Analyse eines Jobs.

    `source` ist "openai" oder "lokal" – so ist immer klar, woher die
    Bewertung stammt.
    """

    fits: bool
    reasons: List[str] = field(default_factory=list)        # max. 5 Punkte
    key_skills: List[str] = field(default_factory=list)     # wichtigste Job-Skills
    recommendation: str = ""                                # 1-2 Sätze
    source: str = "lokal"

    @property
    def verdict(self) -> str:
        return "passt" if self.fits else "passt nicht"

    @classmethod
    def from_local(cls, result: "MatchResult") -> "JobAnalysis":
        """Baut eine Analyse rein aus den lokalen Match-Daten (Fallback)."""
        reasons = list(result.positive_reasons[:4])
        if result.negative_reasons:
            reasons.append("Achtung: " + result.negative_reasons[0])
        rec_map = {
            RECO_SEHR_GUT: "Sehr gute Passung – Bewerbung wird empfohlen.",
            RECO_GUT: "Gute Passung – Bewerbung lohnt sich.",
            RECO_PRUEFEN: "Teilweise Passung – vor der Bewerbung Details prüfen.",
            RECO_ABLEHNEN: "Geringe Passung – eher nicht bewerben.",
        }
        return cls(
            fits=result.score >= 60,  # alles außer "Ablehnen"
            reasons=reasons[:5] or ["Lokale Bewertung ohne weitere Begründung"],
            key_skills=list(result.skills_to_emphasize[:6]),
            recommendation=rec_map.get(result.recommendation, ""),
            source="lokal",
        )


@dataclass
class Profile:
    """Das Bewerber-Profil aus config/profile.yaml."""

    name: str = ""
    email: str = ""
    phone: str = ""
    street: str = ""
    city: str = ""        # "PLZ Ort", z. B. "12345 Musterstadt"
    target_roles: List[str] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    projects: List[str] = field(default_factory=list)
    locations: List[str] = field(default_factory=list)
    open_to_career_change: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> "Profile":
        contact = data.get("contact") if isinstance(data.get("contact"), dict) else {}
        return cls(
            name=data.get("name", ""),
            email=contact.get("email", "") or data.get("email", ""),
            phone=contact.get("phone", ""),
            street=contact.get("street", ""),
            city=contact.get("city", "") or contact.get("plz_ort", ""),
            target_roles=list(data.get("target_roles", []) or []),
            skills=list(data.get("skills", []) or []),
            projects=list(data.get("projects", []) or []),
            locations=list(data.get("location", []) or data.get("locations", []) or []),
            open_to_career_change=bool(data.get("open_to_career_change", True)),
        )
