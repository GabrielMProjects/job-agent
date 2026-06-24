"""Erzeugt einfache Bewerbungs-/Anschreiben-ENTWÜRFE.

WICHTIG: Es wird NICHTS automatisch versendet. Die Dateien sind reine
Entwürfe (Drafts) zum manuellen Prüfen und Anpassen.

Wenn ein OpenAI-Key vorhanden ist, wird der Anschreiben-Text per KI erzeugt.
Ohne Key (oder bei einem Fehler) greift automatisch der lokale Generator.
"""
from __future__ import annotations

import re
import shutil
from datetime import date
from pathlib import Path
from typing import List, Optional, Tuple

import config_loader
import openai_client
from models import MatchResult, Profile

# Quelle der persönlichen PDFs (Lebenslauf, Zeugnisse) – wird nur kopiert.
ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = ROOT / "src" / "pdf"

DOC_TEMPLATE = """# Anschreiben-ENTWURF (DRAFT — NICHT automatisch versenden)

> Dieser Text ist ein automatisch erzeugter Entwurf. Bitte prüfen, anpassen
> und selbst verschicken. Der Job-Agent versendet keine Bewerbungen.

- **Stelle:** {title}
- **Unternehmen:** {company}
- **Ort:** {location}
- **Link:** {link}
- **Score:** {score} / 100  ({recommendation})
- **Erstellt mit:** {generator}

---

{body}

---

## Notizen für dich (nicht Teil des Anschreibens)

- **Hinweis:** {hint}
- **Skills hervorheben:** {skills_list}
- **Pluspunkte der Stelle:** {positives}
- **Mögliche Hürden:** {negatives}
"""


def _slug(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")[:40] or "job"


def _local_body(result: MatchResult, profile: Profile) -> str:
    """Lokaler Anschreiben-Text ohne KI (bisherige Logik)."""
    job = result.job
    skills = result.skills_to_emphasize or profile.skills[:4]
    skills_sentence = ", ".join(skills[:5]) if skills else "modernen Technologien"
    project_sentence = ""
    if profile.projects:
        project_sentence = (
            "Ein Beispiel ist mein Projekt "
            f"\"{profile.projects[0]}\", das viele der geforderten Themen abdeckt."
        )
    return (
        "Sehr geehrte Damen und Herren,\n\n"
        f"mit großem Interesse habe ich Ihre Stellenausschreibung als **{job.title}** "
        f"bei {job.company} gelesen. Die Position passt sehr gut zu meinem Profil und "
        "meinen praktischen Projekten.\n\n"
        f"In meinen Projekten arbeite ich regelmäßig mit {skills_sentence}. "
        f"{project_sentence}\n\n"
        "Gerne bringe ich diese praktische Erfahrung in Ihr Team ein und freue mich "
        "über die Gelegenheit, mehr über die Stelle zu erfahren.\n\n"
        "Mit freundlichen Grüßen\n"
        f"{profile.name or 'Dein Name'}\n"
        f"{profile.email or 'deine@email.de'}"
    )


def _build_content(result: MatchResult, profile: Profile, use_ai: bool,
                   cv_text: str = "", zeugnis_text: str = "",
                   reference_text: str = "") -> Tuple[str, bool, str]:
    """Liefert (Dokument-Text, used_ai, Anschreiben-Text)."""
    job = result.job
    used_ai = False
    body = ""

    if use_ai:
        try:
            ai_text = openai_client.generate_cover_letter_with_ai(
                job, profile, result, cv_text=cv_text, zeugnis_text=zeugnis_text,
                reference_text=reference_text,
            )
            if ai_text:
                signature = f"\n\nMit freundlichen Grüßen\n{profile.name or 'Dein Name'}"
                # Grußformel nur ergänzen, falls die KI keine geliefert hat
                body = ai_text if "freundlichen Grüßen" in ai_text else ai_text + signature
                used_ai = True
        except Exception as exc:  # robuster Fallback, Key wird NICHT geloggt
            print(f"[openai] Anschreiben per KI nicht möglich, nutze lokalen Generator ({type(exc).__name__}).")

    if not used_ai:
        body = _local_body(result, profile)

    skills = result.skills_to_emphasize or profile.skills[:4]
    generator = f"KI (OpenAI {openai_client.get_model()})" if used_ai else "lokalem Generator"
    content = DOC_TEMPLATE.format(
        title=job.title,
        company=job.company,
        location=job.location,
        link=job.link,
        score=result.score,
        recommendation=result.recommendation,
        generator=generator,
        body=body,
        hint=result.cover_letter_hint or "—",
        skills_list=", ".join(skills) if skills else "—",
        positives="; ".join(result.positive_reasons) or "—",
        negatives="; ".join(result.negative_reasons) or "—",
    )
    return content, used_ai, body


def generate_draft_ex(result: MatchResult, profile: Profile, output_dir: Path,
                      use_ai: bool | None = None,
                      cv_text: str | None = None,
                      zeugnis_text: str | None = None) -> Tuple[Path, bool]:
    """Schreibt einen Entwurf und gibt (Pfad, used_ai) zurück.

    use_ai=None -> automatisch (KI nur, wenn Key + Paket vorhanden).
    cv_text/zeugnis_text=None -> automatisch aus config/cv.md bzw. config/zeugnis.md.
    """
    if use_ai is None:
        use_ai = openai_client.is_available()
    if cv_text is None:
        cv_text = config_loader.load_cv()
    if zeugnis_text is None:
        zeugnis_text = config_loader.load_zeugnis()

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    content, used_ai, _ = _build_content(
        result, profile, use_ai, cv_text, zeugnis_text, config_loader.load_reference())
    path = output_dir / f"{result.job.id}_{_slug(result.job.company)}.md"
    path.write_text(content, encoding="utf-8")
    return path, used_ai


def generate_draft(result: MatchResult, profile: Profile, output_dir: Path) -> Path:
    """Wie generate_draft_ex, gibt aber nur den Pfad zurück (rückwärtskompatibel)."""
    path, _ = generate_draft_ex(result, profile, output_dir)
    return path


def generate_drafts(results: List[MatchResult], profile: Profile,
                    output_dir: Path, min_score: int = 75) -> List[Path]:
    """Erzeugt Entwürfe für alle Matches ab `min_score`."""
    written: List[Path] = []
    for result in results:
        if result.score >= min_score:
            written.append(generate_draft(result, profile, output_dir))
    return written


# ---------------------------------------------------------------------------
# Bewerbungspaket (fertige Dateien zum MANUELLEN Hochladen)
# ---------------------------------------------------------------------------
def _pdf_safe(text: str) -> str:
    """Ersetzt Unicode-Sonderzeichen, die die PDF-Kernschrift (latin-1) nicht kann."""
    repl = {"–": "-", "—": "-", "‘": "'", "’": "'",
            "‚": "'", "“": '"', "”": '"', "„": '"',
            "…": "...", " ": " ", "€": "EUR", "•": "-"}
    for k, v in repl.items():
        text = text.replace(k, v)
    return text.encode("latin-1", "replace").decode("latin-1")


# Layout-Konstanten (A4 in mm), angelehnt an die Referenz-Vorlage
_PAGE_W = 210
_MARGIN_L = 22
_MARGIN_R = 22
_HEADER_H = 26
_HEADER_RGB = (40, 54, 69)   # dunkles Slate, wie die Kopfzeile der Referenz


def _city_town(city: str) -> str:
    """'12345 Musterstadt' -> 'Musterstadt'. Ohne PLZ bleibt der Text unverändert."""
    return re.sub(r"^\s*\d{4,5}\s*", "", (city or "").strip()) or "Ort"


def _render_letter_pdf(result: MatchResult, profile: Profile, body: str, path: Path) -> bool:
    """Erzeugt ein professionelles Anschreiben-PDF (fpdf2): dunkler Balken,
    Absenderblock rechts, Datum, fetter Betreff, Fließtext, Signaturbereich.
    Ohne fpdf2 -> False."""
    try:
        from fpdf import FPDF
    except Exception:
        return False
    try:
        name = profile.name or "Dein Name"
        street = profile.street or "Musterstraße 1"
        city = profile.city or "12345 Musterstadt"
        phone = profile.phone or "0123 4567890"
        email = profile.email or "deine@email.de"

        pdf = FPDF(format="A4")
        pdf.set_auto_page_break(auto=True, margin=20)
        pdf.set_margins(_MARGIN_L, 20, _MARGIN_R)
        pdf.add_page()

        # 1) Dunkler Kopfbalken mit Name (weiß)
        pdf.set_fill_color(*_HEADER_RGB)
        pdf.rect(0, 0, _PAGE_W, _HEADER_H, style="F")
        pdf.set_text_color(255, 255, 255)
        pdf.set_xy(_MARGIN_L, 7)
        pdf.set_font("Helvetica", "B", 18)
        pdf.cell(0, 9, _pdf_safe(name), new_x="LMARGIN", new_y="NEXT")
        pdf.set_x(_MARGIN_L)
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 5, _pdf_safe("Bewerbung"), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)

        # 2) Absenderblock rechtsbündig
        pdf.set_y(_HEADER_H + 8)
        pdf.set_font("Helvetica", "", 9.5)
        for line in (name, street, city, "Telefon: " + phone, "E-Mail: " + email):
            pdf.set_x(_MARGIN_L)
            pdf.cell(0, 5, _pdf_safe(line), align="R", new_x="LMARGIN", new_y="NEXT")

        # 3) Datum rechtsbündig
        pdf.ln(6)
        datum = f"{_city_town(city)}, {date.today().strftime('%d.%m.%Y')}"
        pdf.set_x(_MARGIN_L)
        pdf.cell(0, 5, _pdf_safe(datum), align="R", new_x="LMARGIN", new_y="NEXT")

        # 4) Betreff (fett)
        pdf.ln(10)
        pdf.set_x(_MARGIN_L)
        pdf.set_font("Helvetica", "B", 11.5)
        pdf.multi_cell(0, 6, _pdf_safe(f"Bewerbung als {result.job.title}"),
                       new_x="LMARGIN", new_y="NEXT")

        # 5) Fließtext (Grußformel + Name werden separat gesetzt -> Signaturraum)
        pdf.ln(5)
        pdf.set_font("Helvetica", "", 11)
        marker = "Mit freundlichen Grüßen"
        idx = body.find(marker)
        pre = (body[:idx] if idx != -1 else body).strip()
        for para in pre.split("\n"):
            if para.strip() == "":
                pdf.ln(3)
            else:
                pdf.set_x(_MARGIN_L)
                pdf.multi_cell(0, 6, _pdf_safe(para), align="J",
                               new_x="LMARGIN", new_y="NEXT")

        # 6) Grußformel + Signaturbereich + Name
        pdf.ln(7)
        pdf.set_x(_MARGIN_L)
        pdf.multi_cell(0, 6, "Mit freundlichen Grüßen", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(16)  # Platz für Unterschrift
        pdf.set_x(_MARGIN_L)
        pdf.multi_cell(0, 6, _pdf_safe(name), new_x="LMARGIN", new_y="NEXT")

        pdf.output(str(path))
        return True
    except Exception as exc:
        print(f"[pdf] Anschreiben-PDF nicht erstellt ({type(exc).__name__}).")
        return False


def _find_pdf(*patterns: str) -> Optional[Path]:
    """Sucht im PDF-Ordner die erste passende Datei (z. B. 'Lebenslauf*.pdf')."""
    for pattern in patterns:
        matches = sorted(PDF_DIR.glob(pattern))
        if matches:
            return matches[0]
    return None


_README_TEMPLATE = """Bewerbungspaket – {title} bei {company}
Job-ID: {job_id}
Link: {link}

WICHTIG: Dieses Paket wird NICHT automatisch verschickt.
Der Agent meldet sich NICHT bei Indeed an und sendet nichts.

So bewirbst du dich (manuell):
1. Öffne den Link oben im Browser und logge dich SELBST bei Indeed ein.
2. Anschreiben in Anschreiben.md prüfen/anpassen (Quelle: {generator}).
3. Lade diese Dateien hoch:
   - Lebenslauf.pdf {cv_status}
   - Zeugnisse.pdf {zeugnis_status}
   - Anschreiben.md{pdf_hint}
4. Klicke SELBST auf Absenden.
"""


def create_application_package(result: MatchResult, profile: Profile,
                               applications_dir: Path,
                               link: Optional[str] = None,
                               cv_text: Optional[str] = None,
                               zeugnis_text: Optional[str] = None
                               ) -> Tuple[Path, bool, List[str]]:
    """Legt ein fertiges Bewerbungspaket an und gibt (Ordner, used_ai, Dateien)
    zurück. Es wird ausschliesslich lokal vorbereitet – nichts wird versendet."""
    job = result.job
    pkg_dir = Path(applications_dir) / str(job.id)
    pkg_dir.mkdir(parents=True, exist_ok=True)

    use_ai = openai_client.is_available()
    if cv_text is None:
        cv_text = config_loader.load_cv()
    if zeugnis_text is None:
        zeugnis_text = config_loader.load_zeugnis()

    # 1) Anschreiben (Markdown + professionelles PDF)
    reference_text = config_loader.load_reference()
    content, used_ai, body = _build_content(
        result, profile, use_ai, cv_text, zeugnis_text, reference_text)
    (pkg_dir / "Anschreiben.md").write_text(content, encoding="utf-8")
    files = ["Anschreiben.md"]
    pdf_ok = _render_letter_pdf(result, profile, body, pkg_dir / "Anschreiben.pdf")
    if pdf_ok:
        files.append("Anschreiben.pdf")

    # 2) Persönliche PDFs kopieren (falls vorhanden)
    cv_pdf = _find_pdf("Lebenslauf*.pdf", "Lebenslauf*.PDF")
    zeugnis_pdf = _find_pdf("Zeugnis*.pdf", "Zeugnisse*.pdf", "Zeugnis*.PDF")
    cv_status = "(beigelegt)"
    zeugnis_status = "(beigelegt)"
    if cv_pdf:
        shutil.copy2(cv_pdf, pkg_dir / "Lebenslauf.pdf")
        files.append("Lebenslauf.pdf")
    else:
        cv_status = "(FEHLT – bitte selbst hinzufügen)"
    if zeugnis_pdf:
        shutil.copy2(zeugnis_pdf, pkg_dir / "Zeugnisse.pdf")
        files.append("Zeugnisse.pdf")
    else:
        zeugnis_status = "(FEHLT – bitte selbst hinzufügen)"

    # 3) README mit Hinweis + Link
    generator = f"KI (OpenAI {openai_client.get_model()})" if used_ai else "lokaler Generator"
    readme = _README_TEMPLATE.format(
        title=job.title, company=job.company, job_id=job.id,
        link=link or job.link or "(kein Link hinterlegt)",
        generator=generator, cv_status=cv_status, zeugnis_status=zeugnis_status,
        pdf_hint=" (auch als Anschreiben.pdf)" if pdf_ok else "",
    )
    (pkg_dir / "README.txt").write_text(readme, encoding="utf-8")
    files.append("README.txt")

    return pkg_dir, used_ai, files
