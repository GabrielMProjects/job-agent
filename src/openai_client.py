"""Optionale OpenAI-Integration: intelligente Analyse + Anschreiben.

Idee (bewusst einfach gehalten):
- CV + Zeugnis + Jobbeschreibung werden als EIN gemeinsamer Kontext geschickt.
- OpenAI liefert: passt/passt nicht, kurze Begründung, wichtige Skills,
  kurze Empfehlung – und auf Wunsch ein Anschreiben.

Strikte Grenzen:
- OpenAI ERSETZT das lokale Scoring nicht, es ergänzt nur Text/Erklärung.
- Es werden KEINE Bewerbungen versendet.
- Der API-Key wird NIE ausgegeben oder geloggt.
- Ohne Key (oder ohne `openai`-Paket) läuft alles lokal weiter – ohne Fehler.

Das `openai`-Paket wird nur LAZY (in den Funktionen) importiert.
"""
from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, List, Optional

from models import JobAnalysis

if TYPE_CHECKING:  # nur für Typen, kein Laufzeit-Import
    from models import Job, MatchResult, Profile

DEFAULT_MODEL = "gpt-5.5-mini"
_REQUEST_TIMEOUT = 30  # Sekunden
_CV_MAX = 4000         # Zeichen-Limit für den Kontext (Token sparen)
_ZEUGNIS_MAX = 3000
_JOB_MAX = 1500


# ---------------------------------------------------------------------------
# Key / Verfügbarkeit
# ---------------------------------------------------------------------------
def _get_api_key() -> str:
    """Liest den Key aus der Umgebung. Gibt ihn NICHT aus / loggt ihn NICHT."""
    return os.environ.get("OPENAI_API_KEY", "").strip()


def get_model() -> str:
    return os.environ.get("OPENAI_MODEL", "").strip() or DEFAULT_MODEL


def has_api_key() -> bool:
    return bool(_get_api_key())


def is_available() -> bool:
    """True nur, wenn ein Key gesetzt UND das openai-Paket importierbar ist."""
    if not has_api_key():
        return False
    try:
        import openai  # noqa: F401
    except Exception:
        return False
    return True


# ---------------------------------------------------------------------------
# Interne Helfer
# ---------------------------------------------------------------------------
def _client():
    """Erzeugt den OpenAI-Client. Der Key wird vom SDK aus der Umgebung
    gelesen – wir reichen ihn nicht durch Variablen/Logs."""
    from openai import OpenAI

    return OpenAI(timeout=_REQUEST_TIMEOUT)


def _chat(system_prompt: str, user_prompt: str) -> str:
    """Ein einfacher Chat-Aufruf. Gibt den reinen Text zurück."""
    client = _client()
    resp = client.chat.completions.create(
        model=get_model(),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


def _job_block(job: "Job") -> str:
    return (
        f"Titel: {job.title}\n"
        f"Unternehmen: {job.company}\n"
        f"Ort: {job.location}\n"
        f"Beschreibung:\n{(job.description or '')[:_JOB_MAX]}"
    )


def _context_block(cv_text: str, zeugnis_text: str, job: "Job") -> str:
    """Der gemeinsame Kontext: CV + Zeugnis + Stelle."""
    cv = (cv_text or "").strip()[:_CV_MAX] or "(kein Lebenslauf hinterlegt)"
    zeugnis = (zeugnis_text or "").strip()[:_ZEUGNIS_MAX] or "(keine Zeugnisse hinterlegt)"
    return (
        "=== LEBENSLAUF (CV) ===\n" + cv + "\n\n"
        "=== ZEUGNISSE / QUALIFIKATIONEN ===\n" + zeugnis + "\n\n"
        "=== STELLE ===\n" + _job_block(job)
    )


def _coerce_list(value) -> List[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _analysis_from_raw(raw: str) -> JobAnalysis:
    """Robustes Parsen: bevorzugt JSON, sonst Text-Fallback (kein Fehler)."""
    data = {}
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(raw[start:end + 1])
        except Exception:
            data = {}

    if data:
        fits = bool(data.get("fits", False))
        reasons = _coerce_list(data.get("reasons"))[:5]
        skills = _coerce_list(data.get("key_skills"))
        recommendation = str(data.get("recommendation", "")).strip()
    else:
        low = raw.lower()
        fits = ("passt nicht" not in low) and ("nicht passend" not in low)
        reasons = [raw.strip()[:300]] if raw.strip() else []
        skills = []
        recommendation = raw.strip()[:300]

    return JobAnalysis(
        fits=fits,
        reasons=reasons or ["(keine Begründung erhalten)"],
        key_skills=skills,
        recommendation=recommendation,
        source="openai",
    )


# ---------------------------------------------------------------------------
# Öffentliche Funktionen
# ---------------------------------------------------------------------------
def analyze_job(job: "Job", cv_text: str, zeugnis_text: str,
                local_score: Optional[int] = None,
                local_recommendation: Optional[str] = None) -> JobAnalysis:
    """Bewertet eine Stelle gegen CV + Zeugnis (kombinierter Kontext).

    Raises:
        RuntimeError: wenn OpenAI nicht verfügbar ist (kein Key/Paket).
    """
    if not is_available():
        raise RuntimeError("OpenAI ist nicht verfügbar (kein Key oder Paket fehlt).")

    system_prompt = (
        "Du bist ein nüchterner Recruiting-Assistent. Bewerte anhand von Lebenslauf, "
        "Zeugnissen und Stellenbeschreibung, ob der Bewerber passt. Sei ehrlich und "
        "konkret, erfinde nichts. Antworte AUSSCHLIESSLICH als JSON mit den Schlüsseln: "
        '"fits" (true/false), "reasons" (Liste mit max. 5 kurzen deutschen Stichpunkten), '
        '"key_skills" (Liste der wichtigsten in der Stelle geforderten Skills), '
        '"recommendation" (1-2 deutsche Sätze). Kein Text außerhalb des JSON.'
    )
    user_prompt = _context_block(cv_text, zeugnis_text, job)
    if local_score is not None:
        user_prompt += f"\n\n=== LOKALER SCORE ===\n{local_score} ({local_recommendation})"

    return _analysis_from_raw(_chat(system_prompt, user_prompt))


def generate_cover_letter_with_ai(job: "Job", profile: "Profile",
                                  match_result: "MatchResult",
                                  cv_text: str = "", zeugnis_text: str = "") -> str:
    """Erzeugt ein kurzes, professionelles deutsches Anschreiben auf Basis von
    CV + Zeugnis + Stelle.

    Raises:
        RuntimeError: wenn OpenAI nicht verfügbar ist.
    """
    if not is_available():
        raise RuntimeError("OpenAI ist nicht verfügbar (kein Key oder Paket fehlt).")

    system_prompt = (
        "Du bist ein erfahrener Karriere-Coach und schreibst kurze, professionelle "
        "deutsche Bewerbungsanschreiben. Nutze die echten Angaben aus Lebenslauf und "
        "Zeugnissen, erfinde KEINE Qualifikationen. Max. ca. 200 Wörter. Gib NUR den "
        "Fließtext zurück (Anrede bis Grußformel), ohne Betreff/Adressblock, ohne "
        "eckige Platzhalter."
    )
    skills = ", ".join(match_result.skills_to_emphasize or profile.skills[:6])
    user_prompt = (
        "Schreibe ein Anschreiben für die folgende Stelle. Betone passende Skills "
        f"({skills}) und konkrete Projekte aus dem Lebenslauf.\n\n"
        + _context_block(cv_text, zeugnis_text, job)
    )
    return _chat(system_prompt, user_prompt)
