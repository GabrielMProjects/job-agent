"""Tests für den Telegram-Bot – ohne echten Token / ohne Netzwerk.

Getestet werden:
- Callback-Daten parsen
- Status-Aktionen setzen den richtigen Status (later/reject/done)
- "Bewerben vorbereiten" erstellt einen Entwurf und setzt Status "bewerben"
- Inline-Keyboard-Markup wird korrekt erzeugt
- Rückwärtskompatibilität: /start, /status, /job über handle_command

Ausführen:
    python -m unittest discover -s tests
"""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import telegram_bot as tg  # noqa: E402
from database import Database  # noqa: E402
from models import (  # noqa: E402
    Job,
    MatchResult,
    Profile,
    STATUS_APPLIED,
    STATUS_APPLY,
    STATUS_LATER,
    STATUS_NEW,
    STATUS_REJECTED,
)


def make_profile() -> Profile:
    return Profile(
        name="Test Bewerber",
        email="test@example.de",
        skills=["Docker", "Kubernetes", "AWS"],
        projects=["AWS k3s Fullstack Deployment"],
        open_to_career_change=True,
    )


def make_db_with_job(job_id="1") -> Database:
    db = Database(":memory:")
    result = MatchResult(
        job=Job(id=job_id, title="Junior DevOps Engineer", company="CloudWerk",
                location="Köln / Remote", link="https://example.com/1",
                description="Docker, Kubernetes, AWS"),
        score=92,
        recommendation="Sehr gut",
        positive_reasons=["Kern-Tech passt"],
        negative_reasons=[],
        skills_to_emphasize=["Docker", "Kubernetes", "AWS"],
        cover_letter_hint="Betone: Docker, Kubernetes, AWS.",
    )
    db.upsert_match(result)
    return db


class TestParseCallback(unittest.TestCase):
    def test_valid_actions(self):
        self.assertEqual(tg.parse_callback("apply:12"), ("apply", "12"))
        self.assertEqual(tg.parse_callback("details:1"), ("details", "1"))
        self.assertEqual(tg.parse_callback("done:abc"), ("done", "abc"))

    def test_whitespace_and_case(self):
        self.assertEqual(tg.parse_callback("  APPLY : 7 "), ("apply", "7"))

    def test_invalid(self):
        self.assertEqual(tg.parse_callback(""), (None, None))
        self.assertEqual(tg.parse_callback(None), (None, None))
        self.assertEqual(tg.parse_callback("apply"), (None, None))      # kein ":"
        self.assertEqual(tg.parse_callback("foo:1"), (None, None))      # unbekannt
        self.assertEqual(tg.parse_callback("apply:"), (None, None))     # keine ID


class TestKeyboards(unittest.TestCase):
    def _callback_values(self, markup):
        vals = []
        for row in markup["inline_keyboard"]:
            for btn in row:
                vals.append(btn["callback_data"])
                self.assertIn("text", btn)
        return vals

    def test_list_keyboard(self):
        markup = tg.job_list_keyboard("5")
        vals = self._callback_values(markup)
        self.assertEqual(
            set(vals), {"details:5", "apply:5", "later:5", "reject:5"}
        )

    def test_detail_keyboard(self):
        markup = tg.job_detail_keyboard(5)  # auch int-Eingabe
        vals = self._callback_values(markup)
        self.assertEqual(
            set(vals), {"apply:5", "later:5", "reject:5", "done:5"}
        )

    def test_every_callback_is_parseable(self):
        for markup in (tg.job_list_keyboard("9"), tg.job_detail_keyboard("9")):
            for row in markup["inline_keyboard"]:
                for btn in row:
                    action, jid = tg.parse_callback(btn["callback_data"])
                    self.assertIsNotNone(action)
                    self.assertEqual(jid, "9")


class TestHandleCallback(unittest.TestCase):
    def setUp(self):
        self.db = make_db_with_job("1")
        self.profile = make_profile()

    def tearDown(self):
        self.db.close()

    def test_later_sets_status(self):
        text, kb = tg.handle_callback(self.db, "later:1")
        self.assertEqual(self.db.get_status("1"), STATUS_LATER)
        self.assertEqual(text, "Status gesetzt: später")
        self.assertIsNone(kb)

    def test_reject_sets_status(self):
        text, _ = tg.handle_callback(self.db, "reject:1")
        self.assertEqual(self.db.get_status("1"), STATUS_REJECTED)
        self.assertEqual(text, "Status gesetzt: abgelehnt")

    def test_done_sets_status(self):
        text, _ = tg.handle_callback(self.db, "done:1")
        self.assertEqual(self.db.get_status("1"), STATUS_APPLIED)
        self.assertEqual(text, "Status gesetzt: beworben")

    def test_apply_creates_package_and_sets_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            text, kb = tg.handle_callback(
                self.db, "apply:1", self.profile, tmp
            )
            self.assertEqual(self.db.get_status("1"), STATUS_APPLY)
            self.assertIn("Bewerbungspaket erstellt", text)
            self.assertIn("bewerben", text)
            # Paket landet in <tmp>/<job-id>/ mit Anschreiben + README
            pkg = Path(tmp) / "1"
            self.assertTrue((pkg / "Anschreiben.md").exists())
            self.assertTrue((pkg / "README.txt").exists())
            self.assertIn("NICHT automatisch", (pkg / "README.txt").read_text(encoding="utf-8"))

    def test_apply_without_profile_still_sets_status(self):
        text, _ = tg.handle_callback(self.db, "apply:1")
        self.assertEqual(self.db.get_status("1"), STATUS_APPLY)
        self.assertIn("Status gesetzt: bewerben", text)

    def test_details_returns_text_and_detail_keyboard(self):
        text, kb = tg.handle_callback(self.db, "details:1")
        self.assertIn("Junior DevOps Engineer", text)
        self.assertIsNotNone(kb)
        vals = {b["callback_data"] for row in kb["inline_keyboard"] for b in row}
        self.assertIn("done:1", vals)

    def test_unknown_action(self):
        text, kb = tg.handle_callback(self.db, "foo:1")
        self.assertEqual(text, "Unbekannte Aktion.")
        self.assertIsNone(kb)

    def test_unknown_job(self):
        text, _ = tg.handle_callback(self.db, "later:999")
        self.assertIn("999", text)


class TestRender(unittest.TestCase):
    def test_render_top_has_keyboard_per_job(self):
        db = make_db_with_job("1")
        items = tg.render_top(db)
        db.close()
        # Kopfzeile (ohne Keyboard) + 1 Job (mit Keyboard)
        self.assertIsNone(items[0][1])
        self.assertEqual(len(items), 2)
        self.assertIsNotNone(items[1][1])

    def test_render_job_has_detail_keyboard(self):
        db = make_db_with_job("1")
        text, kb = tg.render_job(db, "1")
        db.close()
        self.assertIn("Junior DevOps Engineer", text)
        self.assertIsNotNone(kb)


class TestBackwardCompatibleCommands(unittest.TestCase):
    def setUp(self):
        self.db = make_db_with_job("1")

    def tearDown(self):
        self.db.close()

    def test_start_shows_help(self):
        self.assertIn("Job-Agent Bot", tg.handle_command(self.db, "/start"))

    def test_status_lists_all_statuses(self):
        out = tg.handle_command(self.db, "/status")
        for s in (STATUS_NEW, STATUS_APPLY, STATUS_LATER, STATUS_REJECTED, STATUS_APPLIED):
            self.assertIn(s, out)

    def test_job_text_command_still_works(self):
        out = tg.handle_command(self.db, "/job 1")
        self.assertIn("Junior DevOps Engineer", out)

    def test_apply_text_command_still_works(self):
        out = tg.handle_command(self.db, "/apply 1")
        self.assertEqual(self.db.get_status("1"), STATUS_APPLY)
        self.assertIn("bewerben", out)


class TestAutoImportConfig(unittest.TestCase):
    """auto_import_config liest die Umgebungsvariablen korrekt (kein Netzwerk)."""

    def setUp(self):
        self._saved = {k: os.environ.pop(k, None) for k in (
            "EMAIL_AUTO_IMPORT_ENABLED",
            "EMAIL_AUTO_IMPORT_INTERVAL_MINUTES",
            "EMAIL_AUTO_IMPORT_INCLUDE_SEEN",
        )}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_disabled_by_default(self):
        enabled, interval, include_seen = tg.auto_import_config()
        self.assertFalse(enabled)
        self.assertEqual(interval, 30)        # Default
        self.assertFalse(include_seen)

    def test_enabled_and_interval_read(self):
        os.environ["EMAIL_AUTO_IMPORT_ENABLED"] = "true"
        os.environ["EMAIL_AUTO_IMPORT_INTERVAL_MINUTES"] = "45"
        os.environ["EMAIL_AUTO_IMPORT_INCLUDE_SEEN"] = "yes"
        enabled, interval, include_seen = tg.auto_import_config()
        self.assertTrue(enabled)
        self.assertEqual(interval, 45)
        self.assertTrue(include_seen)

    def test_bad_interval_falls_back_to_30(self):
        os.environ["EMAIL_AUTO_IMPORT_ENABLED"] = "1"
        os.environ["EMAIL_AUTO_IMPORT_INTERVAL_MINUTES"] = "abc"
        _, interval, _ = tg.auto_import_config()
        self.assertEqual(interval, 30)


class TestAutoImportLoop(unittest.TestCase):
    """Der Loop ruft den (mockbaren) Importer auf – ohne echte E-Mail/Telegram."""

    def test_loop_calls_importer(self):
        calls = []
        ev = threading.Event()

        def fake_import():
            calls.append(1)
            ev.set()  # nach dem ersten Lauf stoppen

        tg._auto_import_loop(ev, fake_import, interval_seconds=0.01)
        self.assertEqual(len(calls), 1)

    def test_loop_survives_importer_error(self):
        calls = []
        ev = threading.Event()

        def boom():
            calls.append(1)
            ev.set()
            raise RuntimeError("IMAP kaputt")

        # darf NICHT werfen – Fehler wird abgefangen, Loop endet über das Event
        tg._auto_import_loop(ev, boom, interval_seconds=0.01)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
