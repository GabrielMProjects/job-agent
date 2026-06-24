"""Telegram-Bot (Version 2) mit InlineKeyboard-Buttons.

Sicherheitsregeln (unverändert):
- Startet NUR, wenn TELEGRAM_BOT_TOKEN gesetzt ist.
- Es werden KEINE Bewerbungen versendet.
- Befehle/Buttons ändern ausschließlich lokale Daten in der SQLite-Datenbank
  bzw. erzeugen lokale Bewerbungs-ENTWÜRFE in output/applications/.

Implementiert mit der Standard-Library (urllib, Long-Polling). Es wird KEIN
externes Telegram-Paket benötigt.
"""
from __future__ import annotations

import json
import mimetypes
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Optional, Tuple

import config_loader
import openai_client
from application_generator import create_application_package
from database import Database, row_to_job
from models import (
    JobAnalysis,
    MatchResult,
    Profile,
    STATUS_APPLIED,
    STATUS_APPLY,
    STATUS_LATER,
    STATUS_REJECTED,
)

API_BASE = "https://api.telegram.org/bot{token}/{method}"

# Gültige Callback-Aktionen (kurz & robust): "<action>:<id>"
CALLBACK_ACTIONS = {"details", "apply", "later", "reject", "done"}

HELP_TEXT = (
    "🤖 *Job-Agent Bot*\n\n"
    "Befehle:\n"
    "/start – diese Hilfe\n"
    "/top – beste Matches (mit Buttons)\n"
    "/job <id> – Details zu einem Job (mit Buttons)\n"
    "/apply <id> – als *bewerben* markieren\n"
    "/done <id> – als *beworben* markieren\n"
    "/reject <id> – als *abgelehnt* markieren\n"
    "/later <id> – als *später* markieren\n"
    "/status – Übersicht der Status\n\n"
    "Unter /top und /job erscheinen Buttons:\n"
    "📄 Details · ✅ Bewerben vorbereiten · ⏳ Später · ❌ Ablehnen · "
    "✅ Als beworben markieren\n\n"
    "_Hinweis: Es werden keine Bewerbungen automatisch verschickt._"
)


# ---------------------------------------------------------------------------
# Telegram-API (urllib)
# ---------------------------------------------------------------------------
def _api_call(token: str, method: str, params: dict, timeout: int = 35) -> dict:
    url = API_BASE.format(token=token, method=method)
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send_message(token: str, chat_id: str, text: str,
                 reply_markup: Optional[dict] = None) -> None:
    base = {"chat_id": chat_id, "text": text}
    if reply_markup:
        base["reply_markup"] = json.dumps(reply_markup)
    # 1. Versuch mit Markdown-Formatierung
    try:
        _api_call(token, "sendMessage", {**base, "parse_mode": "Markdown"})
        return
    except urllib.error.HTTPError as exc:
        if exc.code != 400:
            print(f"[telegram] sendMessage fehlgeschlagen: HTTP {exc.code}")
            return
        # HTTP 400: meist ein Markdown-Parsing-Problem (z. B. '_' in langen URLs).
        # -> ohne Formatierung erneut senden (Buttons bleiben erhalten).
    except Exception as exc:
        print(f"[telegram] sendMessage fehlgeschlagen: {type(exc).__name__}")
        return
    try:
        _api_call(token, "sendMessage", base)  # Klartext, garantiert parsebar
    except Exception as exc:
        print(f"[telegram] sendMessage (Klartext) fehlgeschlagen: {type(exc).__name__}")


def answer_callback_query(token: str, callback_query_id: str, text: str = "") -> None:
    """Beendet den Lade-Spinner am Button (Telegram erwartet eine Antwort)."""
    try:
        _api_call(token, "answerCallbackQuery", {
            "callback_query_id": callback_query_id,
            "text": text,
        })
    except Exception as exc:
        print(f"[telegram] answerCallbackQuery fehlgeschlagen: {exc}")


def send_document(token: str, chat_id: str, file_path: str, caption: str = "") -> bool:
    """Schickt eine Datei (PDF etc.) in den Chat (multipart/form-data, stdlib)."""
    path = Path(file_path)
    if not path.exists():
        return False
    boundary = "----jobagent" + os.urandom(8).hex()
    with open(path, "rb") as fh:
        file_bytes = fh.read()
    parts = []

    def field(name, value):
        parts.append(("--" + boundary + "\r\n").encode())
        parts.append(('Content-Disposition: form-data; name="%s"\r\n\r\n' % name).encode())
        parts.append((value + "\r\n").encode("utf-8"))

    field("chat_id", str(chat_id))
    if caption:
        field("caption", caption)
    parts.append(("--" + boundary + "\r\n").encode())
    parts.append(('Content-Disposition: form-data; name="document"; filename="%s"\r\n'
                  % path.name).encode("utf-8"))
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    parts.append(("Content-Type: %s\r\n\r\n" % ctype).encode())
    parts.append(file_bytes)
    parts.append(b"\r\n")
    parts.append(("--" + boundary + "--\r\n").encode())
    body = b"".join(parts)

    req = urllib.request.Request(API_BASE.format(token=token, method="sendDocument"), data=body)
    req.add_header("Content-Type", "multipart/form-data; boundary=" + boundary)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            json.loads(resp.read().decode("utf-8"))
        return True
    except Exception as exc:
        print(f"[telegram] sendDocument fehlgeschlagen ({path.name}): {type(exc).__name__}")
        return False


def get_updates(token: str, offset: int, timeout: int = 30) -> list:
    try:
        result = _api_call(token, "getUpdates", {
            "offset": offset,
            "timeout": timeout,
        }, timeout=timeout + 5)
        return result.get("result", [])
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            # Conflict: ein zweiter Prozess pollt mit demselben Token.
            print("[telegram] 409 Conflict: Es pollt bereits ein anderer Bot-Prozess "
                  "mit diesem Token. Bitte nur EINE Instanz laufen lassen.")
        else:
            print(f"[telegram] getUpdates HTTP-Fehler {exc.code}: {exc}")
        time.sleep(3)
        return []
    except Exception as exc:
        print(f"[telegram] getUpdates fehlgeschlagen: {exc}")
        time.sleep(3)
        return []


def flush_pending_updates(token: str) -> int:
    """Verwirft beim Start alle bereits wartenden Updates und liefert den
    nächsten offset. Verhindert, dass nach einem Neustart alte (zuvor
    getippte) Befehle erneut – und damit zum falschen Zeitpunkt – beantwortet
    werden."""
    try:
        result = _api_call(token, "getUpdates", {"offset": -1, "timeout": 0}, timeout=10)
        updates = result.get("result", [])
        if updates:
            return updates[-1]["update_id"] + 1
    except Exception as exc:
        print(f"[telegram] Konnte alte Updates nicht leeren: {exc}")
    return 0


# ---------------------------------------------------------------------------
# InlineKeyboard-Markup
# ---------------------------------------------------------------------------
def job_list_keyboard(job_id) -> dict:
    """Buttons unter einem Job in der /top-Liste."""
    jid = str(job_id)
    return {"inline_keyboard": [
        [{"text": "📄 Details", "callback_data": f"details:{jid}"},
         {"text": "✅ Bewerben vorbereiten", "callback_data": f"apply:{jid}"}],
        [{"text": "⏳ Später", "callback_data": f"later:{jid}"},
         {"text": "❌ Ablehnen", "callback_data": f"reject:{jid}"}],
    ]}


def job_detail_keyboard(job_id) -> dict:
    """Buttons unter der /job-Detailansicht."""
    jid = str(job_id)
    return {"inline_keyboard": [
        [{"text": "✅ Bewerben vorbereiten", "callback_data": f"apply:{jid}"},
         {"text": "⏳ Später", "callback_data": f"later:{jid}"}],
        [{"text": "❌ Ablehnen", "callback_data": f"reject:{jid}"},
         {"text": "✅ Als beworben markieren", "callback_data": f"done:{jid}"}],
    ]}


# ---------------------------------------------------------------------------
# Callback-Daten parsen
# ---------------------------------------------------------------------------
def parse_callback(data: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """'apply:12' -> ('apply', '12'). Ungültig -> (None, None)."""
    if not data or ":" not in data:
        return (None, None)
    action, _, job_id = data.partition(":")
    action = action.strip().lower()
    job_id = job_id.strip()
    if action not in CALLBACK_ACTIONS or not job_id:
        return (None, None)
    return (action, job_id)


# ---------------------------------------------------------------------------
# Formatierung
# ---------------------------------------------------------------------------
def _format_job_row(row: sqlite3.Row) -> str:
    pos = "; ".join(json.loads(row["positive_reasons"] or "[]")) or "—"
    neg = "; ".join(json.loads(row["negative_reasons"] or "[]")) or "—"
    skills = ", ".join(json.loads(row["skills_to_emphasize"] or "[]")) or "—"
    return (
        f"*{row['title']}* (#{row['id']})\n"
        f"🏢 {row['company']} – {row['location']}\n"
        f"⭐ Score: {row['score']} ({row['recommendation']})\n"
        f"📌 Status: {row['status']}\n"
        f"🔗 {row['link']}\n\n"
        f"➕ {pos}\n"
        f"➖ {neg}\n"
        f"🛠 Skills: {skills}\n"
        f"💡 {row['cover_letter_hint']}"
    )


def _row_to_match_result(row: sqlite3.Row) -> MatchResult:
    """Baut aus einer DB-Zeile ein MatchResult (für die Entwurfs-Erstellung)."""
    return MatchResult(
        job=row_to_job(row),
        score=row["score"],
        recommendation=row["recommendation"],
        positive_reasons=json.loads(row["positive_reasons"] or "[]"),
        negative_reasons=json.loads(row["negative_reasons"] or "[]"),
        skills_to_emphasize=json.loads(row["skills_to_emphasize"] or "[]"),
        cover_letter_hint=row["cover_letter_hint"] or "",
    )


def _build_analysis(row: sqlite3.Row, cv_text: str, zeugnis_text: str) -> JobAnalysis:
    """KI-Analyse, wenn ein Key da ist – sonst lokale Bewertung (Fallback)."""
    result = _row_to_match_result(row)
    if openai_client.is_available():
        try:
            return openai_client.analyze_job(
                result.job, cv_text, zeugnis_text, result.score, result.recommendation
            )
        except Exception as exc:  # Key wird NICHT geloggt
            print(f"[openai] Analyse nicht möglich, nutze lokale Bewertung ({type(exc).__name__}).")
    return JobAnalysis.from_local(result)


def _format_analysis(a: JobAnalysis) -> str:
    quelle = "🤖 OpenAI" if a.source == "openai" else "🧮 lokal"
    lines = [f"*Bewertung ({quelle}):* {a.verdict}"]
    if a.reasons:
        lines.append("Begründung:")
        lines.extend(f"• {r}" for r in a.reasons[:5])
    if a.key_skills:
        lines.append("Wichtige Skills: " + ", ".join(a.key_skills))
    if a.recommendation:
        lines.append(f"Empfehlung: {a.recommendation}")
    return "\n".join(lines)


def _job_detail_text(row: sqlite3.Row, cv_text: str, zeugnis_text: str) -> str:
    """Lokale Details + (KI- oder lokale) Bewertung."""
    analysis = _build_analysis(row, cv_text, zeugnis_text)
    return _format_job_row(row) + "\n\n" + _format_analysis(analysis)


# ---------------------------------------------------------------------------
# Render-Funktionen (liefern Text + optionales Keyboard, ohne Netzwerk)
# ---------------------------------------------------------------------------
def render_top(db: Database, limit: int = 5) -> list:
    """Liefert eine Liste von (text, keyboard) – Kopfzeile + ein Eintrag je Job."""
    rows = db.get_top(limit=limit)
    if not rows:
        return [("Noch keine Matches. Führe zuerst `python src/main.py match` aus.", None)]
    items = [("*Top Matches* – Buttons unter jedem Job:", None)]
    for r in rows:
        text = (
            f"#{r['id']} – ⭐ {r['score']} ({r['recommendation']})\n"
            f"*{r['title']}*\n"
            f"🏢 {r['company']} – {r['location']}\n"
            f"📌 Status: {r['status']}"
        )
        items.append((text, job_list_keyboard(r["id"])))
    return items


def render_job(db: Database, job_id: Optional[str],
               cv_text: str = "", zeugnis_text: str = "") -> Tuple[str, Optional[dict]]:
    if not job_id:
        return ("Bitte eine ID angeben: /job <id>", None)
    row = db.get(job_id)
    if not row:
        return (f"Kein Job mit ID {job_id} gefunden.", None)
    return (_job_detail_text(row, cv_text, zeugnis_text), job_detail_keyboard(job_id))


# ---------------------------------------------------------------------------
# Callback-Logik (testbar, ohne Netzwerk)
# ---------------------------------------------------------------------------
def handle_callback(db: Database, data: Optional[str],
                    profile: Optional[Profile] = None,
                    applications_dir: Optional[str] = None,
                    cv_text: str = "", zeugnis_text: str = ""
                    ) -> Tuple[str, Optional[dict]]:
    """Verarbeitet einen Button-Klick. Gibt (Antworttext, Keyboard) zurück."""
    action, job_id = parse_callback(data)
    if action is None:
        return ("Unbekannte Aktion.", None)

    row = db.get(job_id)
    if not row:
        return (f"Kein Job mit ID {job_id} gefunden.", None)

    if action == "details":
        return (_job_detail_text(row, cv_text, zeugnis_text), job_detail_keyboard(job_id))

    if action == "apply":
        info = ""
        used_ai = False
        if profile is not None and applications_dir is not None:
            try:
                pkg_dir, used_ai, files = create_application_package(
                    _row_to_match_result(row), profile, Path(applications_dir),
                    link=row["link"], cv_text=cv_text, zeugnis_text=zeugnis_text,
                )
                info = (
                    f"\n📎 {', '.join(files)}"
                    f"\n🔗 {row['link']}"
                    f"\n⤴ Anhänge folgen hier im Chat. Bewerbung wird NICHT automatisch "
                    f"verschickt – bitte selbst bei Indeed hochladen."
                )
            except Exception as exc:
                info = f"\n(Paket konnte nicht erstellt werden: {exc})"
        db.set_status(job_id, STATUS_APPLY)
        headline = "✅ KI-Bewerbungspaket erstellt" if used_ai else "✅ Bewerbungspaket erstellt"
        return (
            f"{headline}\nStatus gesetzt: {STATUS_APPLY}{info}",
            None,
        )

    status_map = {
        "later": STATUS_LATER,
        "reject": STATUS_REJECTED,
        "done": STATUS_APPLIED,
    }
    new_status = status_map[action]
    db.set_status(job_id, new_status)
    return (f"Status gesetzt: {new_status}", None)


# ---------------------------------------------------------------------------
# Befehls-Logik für reine Textbefehle (rückwärtskompatibel)
# ---------------------------------------------------------------------------
def handle_command(db: Database, text: str) -> str:
    text = (text or "").strip()
    if not text.startswith("/"):
        return "Sende /start für die Hilfe."

    parts = text.split()
    cmd = parts[0].lower().split("@")[0]  # /apply@meinbot -> /apply
    arg = parts[1] if len(parts) > 1 else None

    if cmd in ("/start", "/help"):
        return HELP_TEXT

    if cmd == "/top":
        rows = db.get_top(limit=10)
        if not rows:
            return "Noch keine Matches. Führe zuerst `python src/main.py match` aus."
        lines = ["*Top Matches:*"]
        for r in rows:
            lines.append(f"#{r['id']} – {r['score']} – {r['title']} ({r['recommendation']})")
        return "\n".join(lines)

    if cmd == "/status":
        counts = db.status_counts()
        lines = ["*Status-Übersicht:*"]
        for status, n in counts.items():
            lines.append(f"{status}: {n}")
        return "\n".join(lines)

    if cmd == "/job":
        if not arg:
            return "Bitte eine ID angeben: /job <id>"
        row = db.get(arg)
        if not row:
            return f"Kein Job mit ID {arg} gefunden."
        return _format_job_row(row)

    status_map = {
        "/apply": STATUS_APPLY,
        "/done": STATUS_APPLIED,
        "/reject": STATUS_REJECTED,
        "/later": STATUS_LATER,
    }
    if cmd in status_map:
        if not arg:
            return f"Bitte eine ID angeben: {cmd} <id>"
        new_status = status_map[cmd]
        if db.set_status(arg, new_status):
            return f"✅ Job #{arg} ist jetzt: *{new_status}*"
        return f"Kein Job mit ID {arg} gefunden."

    return "Unbekannter Befehl. Sende /start für die Hilfe."


# ---------------------------------------------------------------------------
# Automatischer E-Mail-Import (Hintergrund-Thread)
# ---------------------------------------------------------------------------
def auto_import_config() -> Tuple[bool, int, bool]:
    """Liest (enabled, interval_minutes, include_seen) aus der Umgebung."""
    truthy = ("1", "true", "yes", "ja", "on")
    enabled = os.environ.get("EMAIL_AUTO_IMPORT_ENABLED", "").strip().lower() in truthy
    try:
        interval = int(os.environ.get("EMAIL_AUTO_IMPORT_INTERVAL_MINUTES", "30").strip() or "30")
    except ValueError:
        interval = 30
    interval = max(1, interval)
    include_seen = os.environ.get("EMAIL_AUTO_IMPORT_INCLUDE_SEEN", "").strip().lower() in truthy
    return enabled, interval, include_seen


def _auto_import_loop(stop_event: "threading.Event", import_fn: Callable[[], None],
                      interval_seconds: float) -> None:
    """Ruft `import_fn` alle `interval_seconds` auf, bis `stop_event` gesetzt ist.
    Fehler werden geloggt, der Loop (und damit der Bot) läuft weiter."""
    while not stop_event.is_set():
        try:
            import_fn()
        except Exception as exc:  # Bot soll NIE wegen Import-Fehler sterben
            print(f"[auto-import] Fehler, Bot läuft weiter ({type(exc).__name__}).")
        stop_event.wait(interval_seconds)


# ---------------------------------------------------------------------------
# Bot-Schleife
# ---------------------------------------------------------------------------
def run(db_path: str, token: Optional[str] = None,
        allowed_chat_id: Optional[str] = None,
        profile: Optional[Profile] = None,
        applications_dir: Optional[str] = None,
        search_cfg: Optional[dict] = None,
        jobs_csv: Optional[str] = None) -> int:
    """Startet den Long-Polling-Bot. Gibt einen Exit-Code zurück."""
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    allowed_chat_id = allowed_chat_id or os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if not token:
        print(
            "TELEGRAM_BOT_TOKEN ist nicht gesetzt.\n"
            "Lege eine .env-Datei an (siehe .env.example) und trage den Token ein.\n"
            "Der Bot wird NICHT gestartet. Das restliche Programm funktioniert "
            "weiterhin lokal."
        )
        return 0

    db = Database(db_path)
    # CV + Zeugnis einmal laden (Kontext für die KI-Analyse / das Anschreiben).
    cv_text = config_loader.load_cv()
    zeugnis_text = config_loader.load_zeugnis()
    ai_note = "mit OpenAI-Analyse" if openai_client.is_available() else "lokal (ohne OpenAI)"
    print(f"Telegram-Bot läuft ({ai_note}, Strg+C zum Beenden). Es werden keine Bewerbungen versendet.")

    # --- Automatischer E-Mail-Import (Option A) ---------------------------
    import email_importer  # lazy, vermeidet jeglichen Import-Zyklus
    auto_enabled, auto_interval, auto_include_seen = auto_import_config()
    auto_active = auto_enabled and email_importer.load_email_config().is_configured()
    print(f"E-Mail-Auto-Import aktiv: {'ja' if auto_active else 'nein'}"
          + (f" | Intervall: {auto_interval} Minuten" if auto_active else ""))
    if auto_enabled and not auto_active:
        print("[auto-import] aktiviert, aber EMAIL_* nicht konfiguriert -> deaktiviert.")

    stop_event = threading.Event()
    if auto_active:
        def _do_import():
            email_importer.run_import(
                dry_run=False, db_path=db_path, profile=profile,
                search_cfg=search_cfg, jobs_csv=jobs_csv, include_seen=auto_include_seen,
            )
        threading.Thread(
            target=_auto_import_loop,
            args=(stop_event, _do_import, auto_interval * 60),
            daemon=True,
        ).start()

    # Wartende Alt-Updates verwerfen, damit nach einem Neustart keine zuvor
    # getippten Befehle erneut (und zum falschen Befehl) beantwortet werden.
    offset = flush_pending_updates(token)
    try:
        while True:
            updates = get_updates(token, offset)
            for update in updates:
                offset = update["update_id"] + 1

                # --- Button-Klicks ---------------------------------------
                if "callback_query" in update:
                    cq = update["callback_query"]
                    cq_id = cq.get("id", "")
                    data = cq.get("data", "")
                    msg = cq.get("message") or {}
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    if allowed_chat_id and chat_id != allowed_chat_id:
                        answer_callback_query(token, cq_id)
                        continue
                    reply, keyboard = handle_callback(
                        db, data, profile, applications_dir, cv_text, zeugnis_text
                    )
                    answer_callback_query(token, cq_id)
                    send_message(token, chat_id, reply, keyboard)
                    # Nach "Bewerben vorbereiten": Paket-Dateien in den Chat schicken
                    act, jid = parse_callback(data)
                    if act == "apply" and applications_dir and jid:
                        pkg = Path(applications_dir) / jid
                        for fname in ("Anschreiben.pdf", "Lebenslauf.pdf", "Zeugnisse.pdf"):
                            fp = pkg / fname
                            if fp.exists():
                                send_document(token, chat_id, str(fp), caption=fname)
                    continue

                # --- Textnachrichten -------------------------------------
                message = update.get("message") or update.get("edited_message")
                if not message:
                    continue
                chat_id = str(message.get("chat", {}).get("id", ""))
                if allowed_chat_id and chat_id != allowed_chat_id:
                    continue  # fremde Chats ignorieren (Sicherheit)

                text = message.get("text", "")
                cmd = text.strip().split()[0].lower().split("@")[0] if text.strip() else ""
                arg = text.strip().split()[1] if len(text.strip().split()) > 1 else None

                if cmd == "/top":
                    for reply, keyboard in render_top(db):
                        send_message(token, chat_id, reply, keyboard)
                elif cmd == "/job":
                    reply, keyboard = render_job(db, arg, cv_text, zeugnis_text)
                    send_message(token, chat_id, reply, keyboard)
                else:
                    send_message(token, chat_id, handle_command(db, text))
    except KeyboardInterrupt:
        print("\nBot beendet.")
    finally:
        stop_event.set()  # Auto-Import-Thread sauber stoppen
        db.close()
    return 0
