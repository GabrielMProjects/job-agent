"""Import von Indeed-Job-Alert-E-Mails über IMAP.

Ablauf:
1. Verbindet sich per IMAP mit DEINEM eigenen Postfach (Gmail/Outlook/generisch).
2. Liest nur ungelesene Indeed-Alert-Mails der letzten N Tage.
3. Extrahiert Jobs (Titel, Firma, Ort, Link, Kurzbeschreibung) aus dem HTML.
4. Dedupliziert, bewertet lokal (Matcher), optional OpenAI ab Schwellwert.
5. Schickt passende Treffer an Telegram (mit Buttons).

Strikte Grenzen:
- KEIN Scraping von Indeed-Webseiten, KEIN Login bei Indeed, KEINE Bewerbung,
  KEINE Captcha-Umgehung. Es werden ausschliesslich DEINE eigenen E-Mails gelesen.
- Passwörter/API-Keys werden NIE geloggt. E-Mail-Inhalte werden nicht komplett
  ausgegeben. Es werden KEINE Mails gelöscht; optional nur als gelesen markiert.

Nur Standard-Library (imaplib, email, html.parser). OpenAI/Telegram sind optional.
"""
from __future__ import annotations

import csv
import datetime
import hashlib
import os
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import List, Optional, Tuple

import config_loader
import openai_client
from database import Database
from matcher import Matcher
from models import Job, MatchResult, Profile

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Anker-Texte, die KEIN Jobtitel sind (Call-to-Action / Footer / Boilerplate)
_CTA_TEXTS = {
    "job ansehen", "job anzeigen", "jetzt bewerben", "ansehen", "bewerben",
    "details", "mehr jobs", "mehr erfahren", "mehr anzeigen", "alle jobs anzeigen",
    "alle jobs", "job speichern", "passt nicht", "nein", "ja", "profil bearbeiten",
    "diese e-mails pausieren", "e-mail-einstellungen", "einstellungen", "abmelden",
    "abbestellen", "hilfebereich", "indeed", "datenschutzerklärung",
    "nutzungsbedingungen", "view job", "apply now",
    "indeed lebenslauf", "mehr job-vorschläge",
}

# Text-Labels (keine Firma/kein Ort) – beim Feld-Mapping ignorieren
_LABELS = {
    "von", "an", "betreff", "gesendet", "einführung", "anstellungsart",
    "leistung", "stellenbeschreibung", "weiterbildungsprogramme",
}

# Teil-Strings, die einen Footer-/Verwaltungs-/Rechtstext kennzeichnen.
# Tauchen sie im Titel oder in Firma/Ort auf, ist es KEINE echte Stelle.
_BOILERPLATE_SUBSTRINGS = (
    "bestätigen", "bestaetigen", "benachrichtigungen verwalten",
    "benachrichtigung verwalten", "alert verwalten", "alerts verwalten",
    "job-benachrichtigung", "job benachrichtigung", "einstellungen verwalten",
    "abmelden", "abbestellen", "e-mail-einstellung", "email-einstellung",
    "datenschutz", "nutzungsbedingung", "impressum", "hilfebereich",
    "profil bearbeiten", "pausieren", "passt nicht", "mehr erfahren",
    "job anzeigen", "job ansehen", "jetzt bewerben", "mehr anzeigen",
    "app herunterladen", "indeed app", "indeed deutschland", "rights reserved",
    "©", "(c)", "alle rechte vorbehalten",
)


# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


@dataclass
class EmailConfig:
    host: str = ""
    port: int = 993
    username: str = ""
    password: str = ""
    folder: str = "INBOX"
    from_filter: str = "indeed"
    lookback_days: int = 3
    min_score_openai: int = 60
    min_score_telegram: int = 75

    def is_configured(self) -> bool:
        return bool(self.host or self.username) and bool(self.username) and bool(self.password)


def load_email_config() -> EmailConfig:
    return EmailConfig(
        host=os.environ.get("EMAIL_IMAP_HOST", "").strip(),
        port=_int_env("EMAIL_IMAP_PORT", 993),
        username=os.environ.get("EMAIL_USERNAME", "").strip(),
        password=os.environ.get("EMAIL_PASSWORD", ""),
        folder=os.environ.get("EMAIL_FOLDER", "INBOX").strip() or "INBOX",
        from_filter=os.environ.get("EMAIL_FROM_FILTER", "indeed").strip() or "indeed",
        lookback_days=_int_env("EMAIL_LOOKBACK_DAYS", 3),
        min_score_openai=_int_env("EMAIL_MIN_SCORE_FOR_OPENAI", 60),
        min_score_telegram=_int_env("EMAIL_MIN_SCORE_FOR_TELEGRAM", 75),
    )


def infer_imap_host(username: str) -> str:
    """Errät den IMAP-Host aus der E-Mail-Domain (Gmail/Outlook/generisch)."""
    domain = username.split("@")[-1].lower() if "@" in username else ""
    if domain in ("gmail.com", "googlemail.com"):
        return "imap.gmail.com"
    if domain in ("outlook.com", "hotmail.com", "live.com", "msn.com", "outlook.de", "hotmail.de"):
        return "outlook.office365.com"
    return f"imap.{domain}" if domain else ""


# ---------------------------------------------------------------------------
# HTML-Parser für Indeed-Alert-Mails
# ---------------------------------------------------------------------------
def _is_job_link(href: str) -> bool:
    # Indeed-Alerts nutzen Tracking-Redirects (cts.indeed.com/v3/...). Es reicht,
    # dass der Link auf eine Indeed-Domain zeigt; Footer/CTA werden über den
    # Ankertext (_looks_like_title) und Href-Dedup herausgefiltert.
    return "indeed" in (href or "").lower()


def _looks_like_title(text: str) -> bool:
    """True nur, wenn der Text wie eine echte Stellenbezeichnung aussieht."""
    t = (text or "").strip()
    if len(t) < 3 or not any(c.isalpha() for c in t):
        return False
    low = t.lower()
    if low in _CTA_TEXTS:
        return False
    return not any(b in low for b in _BOILERPLATE_SUBSTRINGS)


class _IndeedAlertParser(HTMLParser):
    """Sammelt Anker (Links) und Textknoten in Reihenfolge."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: List[Tuple[str, str, str]] = []  # (kind, href, text)
        self._in_a = False
        self._href = ""
        self._buf: List[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "head"):
            self._skip += 1
        elif tag == "a":
            self._in_a = True
            self._href = dict(attrs).get("href", "") or ""
            self._buf = []

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head") and self._skip > 0:
            self._skip -= 1
        elif tag == "a" and self._in_a:
            text = " ".join("".join(self._buf).split())
            self.tokens.append(("a", self._href, text))
            self._in_a = False
            self._href = ""
            self._buf = []

    def handle_data(self, data):
        if self._skip > 0:
            return
        if self._in_a:
            self._buf.append(data)
        else:
            text = " ".join(data.split())
            if text:
                self.tokens.append(("t", "", text))


import re as _re

# Deutsche PLZ + Ort, z. B. "74076 Heilbronn" / "12345 Musterstadt"
_PLZ_CITY = _re.compile(r"\b\d{5}\s+[A-Za-zÄÖÜäöüß][\wÄÖÜäöüß .\-/]{1,40}")
_LOCATION_WORDS = {"remote", "homeoffice", "home office", "deutschland", "hybrid"}


def _looks_like_rating(text: str) -> bool:
    """Indeed-Digest blendet Firmen-Sternebewertungen ein (z. B. '4.3', '(3.5)',
    '★ 4,3'). Solche reinen Bewertungs-Token sind weder Firma noch Ort."""
    t = (text or "").strip().strip("()").replace("★", "").replace("⭐", "").strip()
    return bool(_re.fullmatch(r"[0-5]([.,]\d)?", t))

# Rechtsform-Suffixe = starkes Signal für eine echte Firma
_COMPANY_SUFFIX = _re.compile(
    r"\b(GmbH|gGmbH|mbH|AG|KG|SE|UG|GbR|e\.?\s?V\.?|Ltd|Inc|LLC|PLC|"
    r"Co\.?\s?KG|S\.?A\.?|N\.?V\.?)\b", _re.IGNORECASE)

# Plausible Orte (Substring-Treffer genügt) – ergänzt PLZ-Erkennung
_CITY_HINTS = {
    "köln", "düsseldorf", "essen", "dortmund", "bonn", "duisburg", "aachen",
    "münster", "wuppertal", "bielefeld", "bochum", "krefeld",
    "berlin", "hamburg", "münchen", "frankfurt", "stuttgart", "heilbronn",
    "hannover", "nürnberg", "leipzig", "dresden", "bremen", "karlsruhe",
    "mannheim", "mainz", "kassel", "freiburg", "remote", "homeoffice",
    "deutschland", "hybrid",
}


def _assign_fields(texts: List[str]) -> Tuple[str, str, str]:
    """Bestimmt Firma/Ort/Beschreibung aus den Textzeilen eines Jobs.

    Echtes Indeed-Format: irgendwo steht eine Zeile 'PLZ Ort' (z. B.
    '74076 Heilbronn'); die Firma steht direkt davor. Fällt das aus, gilt die
    einfache Heuristik (1. Zeile Firma, 2. Zeile Ort)."""
    clean = []
    for t in texts:
        s = (t or "").strip()
        if not s:
            continue
        if s.lower() in _CTA_TEXTS or s.lower().rstrip(":") in _LABELS:
            continue
        if _looks_like_rating(s):  # Sternebewertung ignorieren
            continue
        clean.append(s)
    if not clean:
        return "", "", ""

    loc_idx = None
    location = ""
    for i, s in enumerate(clean):
        if _PLZ_CITY.search(s) or s.lower() in _LOCATION_WORDS:
            location, loc_idx = s, i
            break

    if loc_idx is not None:
        company = clean[loc_idx - 1] if loc_idx >= 1 else (clean[0] if clean else "")
        description = " ".join(clean[loc_idx + 1:])[:400]
    else:
        company = clean[0]
        location = clean[1] if len(clean) > 1 else ""
        if not location:
            for sep in (" - ", " • ", " | ", ", "):
                if sep in company:
                    company, location = (p.strip() for p in company.split(sep, 1))
                    break
        description = " ".join(clean[2:])[:400] if len(clean) > 2 else ""

    return company.strip(), location.strip(), description.strip()


def _is_boilerplate(text: str) -> bool:
    low = (text or "").lower()
    return any(b in low for b in _BOILERPLATE_SUBSTRINGS)


def _location_ok(location: str) -> bool:
    if not location:
        return False
    if _PLZ_CITY.search(location):
        return True
    low = location.lower()
    if low in _LOCATION_WORDS:
        return True
    return any(city in low for city in _CITY_HINTS)


def _company_ok(company: str) -> bool:
    return bool(company) and bool(_COMPANY_SUFFIX.search(company))


def _is_plausible_job(job: dict) -> bool:
    """Ein Job zählt nur, wenn der Titel echt aussieht UND Firma oder Ort
    plausibel erkannt wurde (und beides nicht Footer-/Rechtstext ist)."""
    if not _looks_like_title(job.get("title", "")):
        return False
    company, location = job.get("company", ""), job.get("location", "")
    if _is_boilerplate(company) or _is_boilerplate(location):
        return False
    return _company_ok(company) or _location_ok(location)


def parse_jobs_from_html(html: str) -> List[dict]:
    """Extrahiert Jobs aus einer Indeed-Alert-Mail (HTML). Best effort."""
    parser = _IndeedAlertParser()
    try:
        parser.feed(html or "")
    except Exception:
        return []

    jobs: List[dict] = []
    cur: Optional[dict] = None
    pending: List[str] = []
    seen_hrefs: set = set()

    def flush():
        if cur is not None:
            company, location, description = _assign_fields(pending)
            cur.update(company=company, location=location, description=description)
            jobs.append(cur)

    for kind, href, text in parser.tokens:
        # Ein Job-Titel-Anker mit noch unbekanntem Link startet einen neuen Job.
        # Gleiche Links (Titel + "Job anzeigen" + "Mehr erfahren") = derselbe Job.
        is_job = (kind == "a" and _is_job_link(href)
                  and _looks_like_title(text) and href not in seen_hrefs)
        if is_job:
            flush()
            seen_hrefs.add(href)
            cur = {"title": text.strip(), "link": href,
                   "company": "", "location": "", "description": ""}
            pending = []
        elif cur is not None and text:
            pending.append(text)
    flush()
    return [j for j in jobs if _is_plausible_job(j)]


# ---------------------------------------------------------------------------
# Dedup / Job-ID
# ---------------------------------------------------------------------------
def dedupe_key(parsed: dict) -> str:
    """Identität bevorzugt Titel+Firma+Ort, sonst Link (Tracking-Links variieren)."""
    parts = [parsed.get("title", ""), parsed.get("company", ""), parsed.get("location", "")]
    key = "|".join(p.strip().lower() for p in parts if p and p.strip())
    return key or (parsed.get("link", "") or "").strip().lower()


def make_job_id(parsed: dict) -> str:
    digest = hashlib.sha1(dedupe_key(parsed).encode("utf-8")).hexdigest()[:10]
    return f"em-{digest}"


# ---------------------------------------------------------------------------
# Verarbeitung (testbar, ohne Netzwerk)
# ---------------------------------------------------------------------------
def process_parsed_jobs(parsed_jobs: List[dict], db: Optional[Database],
                        profile: Profile, search_cfg: dict, cfg: EmailConfig,
                        notifier: Optional["TelegramNotifier"] = None,
                        jobs_csv: Optional[Path] = None,
                        dry_run: bool = False) -> List[dict]:
    """Dedupliziert, bewertet und (ausser im Dry-Run) speichert/benachrichtigt.

    Gibt eine Zusammenfassung je Job zurück (ohne sensible Inhalte)."""
    matcher = Matcher(profile, search_cfg)
    cv_text = zeugnis_text = ""
    if not dry_run and openai_client.is_available():
        cv_text = config_loader.load_cv()
        zeugnis_text = config_loader.load_zeugnis()

    seen: set = set()
    summaries: List[dict] = []
    csv_rows: List[dict] = []

    for pj in parsed_jobs:
        key = dedupe_key(pj)
        if not key or key in seen:
            continue
        seen.add(key)

        jid = make_job_id(pj)
        description = (pj.get("description") or "").strip()
        from_alert = len(description) < 40
        if from_alert:
            base = " | ".join(p for p in (pj.get("title"), pj.get("company"),
                                          pj.get("location")) if p)
            description = f"[Beschreibung aus Alert-Mail] {base}".strip()

        job = Job(
            id=jid, title=pj.get("title", "").strip(),
            company=pj.get("company", "").strip(),
            location=pj.get("location", "").strip(),
            link=pj.get("link", "").strip(), description=description,
        )
        result: MatchResult = matcher.score_job(job)
        is_new = True if db is None else (db.get(jid) is None)

        notified = False
        analyzed = False
        analysis = None

        if not dry_run:
            if db is not None:
                db.upsert_match(result)
            csv_rows.append({
                "id": job.id, "title": job.title, "company": job.company,
                "location": job.location, "link": job.link, "description": job.description,
            })
            # OpenAI nur für NEUE Jobs ab Schwellwert (Kosten + Doppelimporte sparen)
            if is_new and result.score >= cfg.min_score_openai and openai_client.is_available():
                try:
                    analysis = openai_client.analyze_job(
                        job, cv_text, zeugnis_text, result.score, result.recommendation)
                    analyzed = True
                except Exception as exc:  # Key wird NICHT geloggt
                    print(f"[openai] Analyse übersprungen ({type(exc).__name__}).")
            # Telegram nur für NEUE, ausreichend gute Treffer
            if is_new and result.score >= cfg.min_score_telegram and notifier is not None:
                try:
                    notifier.notify_job(result, analysis)
                    notified = True
                except Exception as exc:
                    print(f"[telegram] Benachrichtigung fehlgeschlagen ({type(exc).__name__}).")

        summaries.append({
            "id": job.id, "title": job.title, "company": job.company,
            "location": job.location, "link": job.link, "score": result.score,
            "recommendation": result.recommendation, "is_new": is_new,
            "notified": notified, "analyzed": analyzed, "from_alert": from_alert,
        })

    if not dry_run and jobs_csv and csv_rows:
        append_jobs_to_csv(Path(jobs_csv), csv_rows)

    return summaries


def append_jobs_to_csv(path: Path, rows: List[dict]) -> int:
    """Hängt neue Jobs an data/jobs.csv an (ohne Duplikate nach id)."""
    path = Path(path)
    existing_ids = set()
    if path.exists():
        with open(path, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("id"):
                    existing_ids.add(row["id"].strip())

    fields = ["id", "title", "company", "location", "link", "description"]
    new_rows = [r for r in rows if r["id"] not in existing_ids]
    if not new_rows:
        return 0
    write_header = not path.exists()
    with open(path, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if write_header:
            writer.writeheader()
        for r in new_rows:
            writer.writerow({k: r.get(k, "") for k in fields})
    return len(new_rows)


# ---------------------------------------------------------------------------
# Telegram-Benachrichtigung
# ---------------------------------------------------------------------------
class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id

    @classmethod
    def from_env(cls) -> Optional["TelegramNotifier"]:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if token and chat_id:
            return cls(token, chat_id)
        return None

    def notify_job(self, result: MatchResult, analysis=None) -> None:
        import telegram_bot as tg
        job = result.job
        reasons = "; ".join(result.positive_reasons[:3]) or "—"
        text = (
            "🆕 *Neuer Treffer aus Indeed-Alert*\n"
            f"*{job.title}*\n"
            f"🏢 {job.company} – {job.location}\n"
            f"⭐ Score: {result.score} ({result.recommendation})\n"
            f"➕ {reasons}\n"
            f"🔗 {job.link}"
        )
        if analysis is not None:
            first = analysis.reasons[0] if analysis.reasons else ""
            text += f"\n🤖 {analysis.verdict}: {first}"
        tg.send_message(self.token, self.chat_id, text, tg.job_list_keyboard(job.id))


# ---------------------------------------------------------------------------
# IMAP I/O
# ---------------------------------------------------------------------------
def _imap_date(d: datetime.date) -> str:
    return f"{d.day:02d}-{_MONTHS[d.month - 1]}-{d.year}"


def _open_imap(cfg: EmailConfig):
    import imaplib
    host = cfg.host or infer_imap_host(cfg.username)
    if not host:
        raise ValueError("Kein IMAP-Host bekannt (EMAIL_IMAP_HOST setzen).")
    imap = imaplib.IMAP4_SSL(host, cfg.port)
    imap.login(cfg.username, cfg.password)
    imap.select(cfg.folder)
    return imap


def _extract_html(msg) -> str:
    """Bevorzugt text/html, sonst text/plain."""
    html_parts: List[str] = []
    text_parts: List[str] = []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        ctype = part.get_content_type()
        if ctype not in ("text/html", "text/plain"):
            continue
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="replace")
        except Exception:
            continue
        if ctype == "text/html":
            html_parts.append(decoded)
        else:
            text_parts.append(decoded)
    if html_parts:
        return "\n".join(html_parts)
    # text/plain in minimalen HTML-Ersatz packen (Links bleiben als Text erhalten)
    return "\n".join(text_parts)


def _fetch_messages(imap, cfg: EmailConfig,
                    include_seen: bool = False) -> List[Tuple[bytes, str]]:
    import email as email_mod
    since = _imap_date(datetime.date.today() - datetime.timedelta(days=cfg.lookback_days))
    criteria = ["SINCE", since]
    if not include_seen:
        criteria = ["UNSEEN"] + criteria
    if cfg.from_filter:
        criteria += ["FROM", cfg.from_filter]
    typ, data = imap.search(None, *criteria)
    out: List[Tuple[bytes, str]] = []
    if typ != "OK" or not data or not data[0]:
        return out
    for num in data[0].split():
        # BODY.PEEK[] liest die Mail OHNE sie als gelesen zu markieren.
        # (RFC822 würde das \Seen-Flag automatisch setzen – unerwünscht im Dry-Run.)
        typ, msg_data = imap.fetch(num, "(BODY.PEEK[])")
        if typ != "OK" or not msg_data or not msg_data[0]:
            continue
        msg = email_mod.message_from_bytes(msg_data[0][1])
        html = _extract_html(msg)
        if html:
            out.append((num, html))
    return out


def _mark_seen(imap, num: bytes) -> None:
    imap.store(num, "+FLAGS", "\\Seen")


# ---------------------------------------------------------------------------
# Ausgabe
# ---------------------------------------------------------------------------
def _print_summary(summaries: List[dict], cfg: EmailConfig, dry_run: bool) -> None:
    if not summaries:
        print("Keine Jobs aus den E-Mails extrahiert.")
        return
    print(f"\n{len(summaries)} eindeutige(r) Job(s):")
    for s in sorted(summaries, key=lambda x: x["score"], reverse=True):
        flags = []
        if s["is_new"]:
            flags.append("NEU")
        if s["notified"]:
            flags.append("→Telegram")
        if s["analyzed"]:
            flags.append("KI")
        if s["from_alert"]:
            flags.append("Kurzbeschr.")
        tag = ("  [" + ", ".join(flags) + "]") if flags else ""
        print(f"  [{s['score']:>3}] {s['recommendation']:<9} {s['title']} – "
              f"{s['company']} ({s['location']}){tag}")
    notified = sum(1 for s in summaries if s["notified"])
    print(f"\nTelegram-Benachrichtigungen: {notified} "
          f"(Schwellwert Score >= {cfg.min_score_telegram})")
    if dry_run:
        print("DRY-RUN: Es wurde NICHTS gespeichert, gesendet oder als gelesen markiert.")


# ---------------------------------------------------------------------------
# Top-Level (CLI)
# ---------------------------------------------------------------------------
def run_import(dry_run: bool = False, db_path: Optional[str] = None,
               profile: Optional[Profile] = None, search_cfg: Optional[dict] = None,
               jobs_csv: Optional[Path] = None, include_seen: bool = False) -> int:
    cfg = load_email_config()
    mode = "DRY-RUN (nichts wird gespeichert)" if dry_run else "Import"
    print(f"Indeed-Alert E-Mail-Import – {mode}")

    if not cfg.is_configured():
        print("Nicht konfiguriert. Bitte EMAIL_IMAP_HOST / EMAIL_USERNAME / "
              "EMAIL_PASSWORD in der .env setzen (siehe .env.example).")
        return 0

    try:
        imap = _open_imap(cfg)
    except Exception as exc:  # Passwort wird NICHT ausgegeben
        print(f"IMAP-Verbindung/Anmeldung fehlgeschlagen: {type(exc).__name__}. "
              "Host/Benutzer/App-Passwort prüfen.")
        return 1

    db: Optional[Database] = None
    try:
        messages = _fetch_messages(imap, cfg, include_seen=include_seen)
        scope = "inkl. gelesen" if include_seen else "ungelesen"
        print(f"{len(messages)} passende E-Mail(s) gefunden "
              f"({scope}, letzte {cfg.lookback_days} Tage, FROM~'{cfg.from_filter}').")

        parsed: List[dict] = []
        for _, html in messages:
            parsed.extend(parse_jobs_from_html(html))

        if not dry_run:
            db = Database(db_path)
        notifier = None if dry_run else TelegramNotifier.from_env()
        if not dry_run and notifier is None:
            print("Hinweis: TELEGRAM_BOT_TOKEN/CHAT_ID fehlen – keine Telegram-Benachrichtigung.")

        summaries = process_parsed_jobs(
            parsed, db, profile, search_cfg or {}, cfg,
            notifier=notifier, jobs_csv=(None if dry_run else jobs_csv), dry_run=dry_run,
        )
        _print_summary(summaries, cfg, dry_run)

        if not dry_run:
            # Mails erst NACH erfolgreicher Verarbeitung als gelesen markieren
            for num, _ in messages:
                try:
                    _mark_seen(imap, num)
                except Exception:
                    pass
        return 0
    except Exception as exc:
        print(f"Fehler beim Verarbeiten: {type(exc).__name__}: {exc}")
        return 1
    finally:
        if db is not None:
            db.close()
        try:
            imap.logout()
        except Exception:
            pass
