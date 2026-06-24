"""Job-Agent – CLI-Einstiegspunkt.

Befehle:
    python src/main.py match       -> bewertet alle Jobs, schreibt DB + matches.csv
    python src/main.py top         -> zeigt die besten Matches in der Konsole
    python src/main.py generate    -> erstellt Bewerbungs-Entwürfe (Score >= 75)
    python src/main.py telegram    -> startet den Telegram-Bot (nur mit Token)
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import List

# Projekt-Pfade
ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"

PROFILE_PATH = CONFIG_DIR / "profile.yaml"
SEARCH_PATH = CONFIG_DIR / "search.yaml"
JOBS_PATH = DATA_DIR / "jobs.csv"
DB_PATH = DATA_DIR / "job_agent.sqlite"
MATCHES_CSV = OUTPUT_DIR / "matches.csv"
APPLICATIONS_DIR = OUTPUT_DIR / "applications"
ENV_PATH = ROOT / ".env"

# src/ auf den Importpfad legen (für direkten Aufruf)
sys.path.insert(0, str(Path(__file__).resolve().parent))

from application_generator import create_application_package, generate_drafts  # noqa: E402
from config_loader import load_env, load_profile, load_search_config  # noqa: E402
from database import Database  # noqa: E402
from matcher import Matcher  # noqa: E402
from models import Job, MatchResult  # noqa: E402

MATCHES_FIELDS = [
    "id", "title", "company", "location", "link", "score", "recommendation",
    "positive_reasons", "negative_reasons", "skills_to_emphasize",
    "cover_letter_hint",
]
GENERATE_MIN_SCORE = 75


# ---------------------------------------------------------------------------
# Laden / Speichern
# ---------------------------------------------------------------------------
def load_jobs(path: Path) -> List[Job]:
    jobs: List[Job] = []
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not (row.get("id") or "").strip():
                continue
            jobs.append(Job(
                id=row["id"].strip(),
                title=(row.get("title") or "").strip(),
                company=(row.get("company") or "").strip(),
                location=(row.get("location") or "").strip(),
                link=(row.get("link") or "").strip(),
                description=(row.get("description") or "").strip(),
            ))
    return jobs


def write_matches_csv(results: List[MatchResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(results, key=lambda r: r.score, reverse=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(MATCHES_FIELDS)
        for r in ordered:
            writer.writerow([
                r.job.id, r.job.title, r.job.company, r.job.location, r.job.link,
                r.score, r.recommendation,
                "; ".join(r.positive_reasons),
                "; ".join(r.negative_reasons),
                "; ".join(r.skills_to_emphasize),
                r.cover_letter_hint,
            ])


# ---------------------------------------------------------------------------
# Befehle
# ---------------------------------------------------------------------------
def cmd_match(args: argparse.Namespace) -> int:
    profile = load_profile(PROFILE_PATH)
    search_cfg = load_search_config(SEARCH_PATH)
    jobs = load_jobs(JOBS_PATH)
    if not jobs:
        print("Keine Jobs in data/jobs.csv gefunden.")
        return 1

    matcher = Matcher(profile, search_cfg)
    results = matcher.score_all(jobs)

    with Database(DB_PATH) as db:
        db.save_all(results)

    write_matches_csv(results, MATCHES_CSV)

    sehr_gut = sum(1 for r in results if r.recommendation == "Sehr gut")
    gut = sum(1 for r in results if r.recommendation == "Gut")
    pruefen = sum(1 for r in results if r.recommendation == "Prüfen")
    ablehnen = sum(1 for r in results if r.recommendation == "Ablehnen")

    print(f"{len(results)} Jobs bewertet.")
    print(f"  Sehr gut: {sehr_gut} | Gut: {gut} | Prüfen: {pruefen} | Ablehnen: {ablehnen}")
    print(f"Datenbank: {DB_PATH}")
    print(f"Matches:   {MATCHES_CSV}")
    print("Tipp: 'python src/main.py generate' erstellt Entwürfe für Jobs ab 75 Punkten.")
    return 0


def cmd_top(args: argparse.Namespace) -> int:
    limit = getattr(args, "limit", 10)
    with Database(DB_PATH) as db:
        rows = db.get_top(limit=limit)
    if not rows:
        print("Keine Matches gefunden. Führe zuerst 'python src/main.py match' aus.")
        return 1
    print(f"Top {len(rows)} Matches:\n")
    for r in rows:
        print(f"  #{r['id']:>3}  {r['score']:>3}  {r['recommendation']:<9}  "
              f"{r['title']} – {r['company']} ({r['location']})")
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    profile = load_profile(PROFILE_PATH)
    search_cfg = load_search_config(SEARCH_PATH)
    jobs = load_jobs(JOBS_PATH)
    if not jobs:
        print("Keine Jobs in data/jobs.csv gefunden. Bitte zuerst 'match' ausführen.")
        return 1

    matcher = Matcher(profile, search_cfg)
    results = matcher.score_all(jobs)
    written = generate_drafts(results, profile, APPLICATIONS_DIR, GENERATE_MIN_SCORE)

    print(f"{len(written)} Bewerbungs-Entwürfe erstellt (Score >= {GENERATE_MIN_SCORE}):")
    for p in written:
        print(f"  {p}")
    if not written:
        print("  (keine Jobs über dem Schwellwert)")
    print("\nHinweis: Es wurde NICHTS versendet. Bitte Entwürfe manuell prüfen.")
    return 0


def cmd_package(args: argparse.Namespace) -> int:
    profile = load_profile(PROFILE_PATH)
    search_cfg = load_search_config(SEARCH_PATH)
    jobs = load_jobs(JOBS_PATH)
    if not jobs:
        print("Keine Jobs in data/jobs.csv gefunden. Bitte zuerst 'match' ausführen.")
        return 1

    matcher = Matcher(profile, search_cfg)
    results = matcher.score_all(jobs)

    target_id = getattr(args, "id", None)
    min_score = getattr(args, "min_score", GENERATE_MIN_SCORE)
    if target_id:
        results = [r for r in results if r.job.id == target_id]
        if not results:
            print(f"Kein Job mit ID {target_id} gefunden.")
            return 1
    else:
        results = [r for r in results if r.score >= min_score]

    if not results:
        print("Keine passenden Jobs (Schwellwert oder ID).")
        return 0

    for r in results:
        pkg_dir, used_ai, files = create_application_package(r, profile, APPLICATIONS_DIR)
        marker = "KI" if used_ai else "lokal"
        print(f"📁 {pkg_dir}  ({marker})  -> {', '.join(files)}")

    print("\nHinweis: Es wurde NICHTS versendet. Bitte Pakete manuell bei Indeed hochladen.")
    return 0


def cmd_import_email(args: argparse.Namespace) -> int:
    load_env(ENV_PATH)
    profile = load_profile(PROFILE_PATH)
    search_cfg = load_search_config(SEARCH_PATH)
    import email_importer
    return email_importer.run_import(
        dry_run=getattr(args, "dry_run", False),
        db_path=str(DB_PATH),
        profile=profile,
        search_cfg=search_cfg,
        jobs_csv=JOBS_PATH,
    )


def cmd_telegram(args: argparse.Namespace) -> int:
    load_env(ENV_PATH)
    import telegram_bot
    profile = load_profile(PROFILE_PATH)
    search_cfg = load_search_config(SEARCH_PATH)
    return telegram_bot.run(
        str(DB_PATH),
        profile=profile,
        applications_dir=str(APPLICATIONS_DIR),
        search_cfg=search_cfg,
        jobs_csv=str(JOBS_PATH),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="job-agent",
        description="Lokaler Bewerbungs- und Job-Matching-Agent.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("match", help="Jobs bewerten, in DB + matches.csv speichern")

    p_top = sub.add_parser("top", help="beste Matches anzeigen")
    p_top.add_argument("--limit", type=int, default=10, help="Anzahl (Default 10)")

    sub.add_parser("generate", help="Bewerbungs-Entwürfe (Score >= 75) erzeugen")

    p_pkg = sub.add_parser("package", help="Bewerbungspaket(e) zum manuellen Hochladen erstellen")
    p_pkg.add_argument("--id", help="nur dieser Job (sonst alle ab Score 75)")
    p_pkg.add_argument("--min-score", type=int, default=GENERATE_MIN_SCORE,
                       help=f"Mindest-Score (Default {GENERATE_MIN_SCORE})")

    p_email = sub.add_parser("import-email",
                             help="Indeed-Alert-Mails per IMAP importieren und bewerten")
    p_email.add_argument("--dry-run", action="store_true",
                         help="Mails lesen + Treffer zeigen, aber nichts speichern/senden")

    sub.add_parser("telegram", help="Telegram-Bot starten (nur mit Token)")
    return parser


def main(argv: List[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # .env laden, damit z. B. OPENAI_API_KEY auch bei 'generate' verfügbar ist.
    load_env(ENV_PATH)

    handlers = {
        "match": cmd_match,
        "top": cmd_top,
        "generate": cmd_generate,
        "package": cmd_package,
        "import-email": cmd_import_email,
        "telegram": cmd_telegram,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
