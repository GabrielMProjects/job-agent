"""SQLite-Persistenz für Matches und lokale Bewerbungs-Status.

Nur Standard-Library (sqlite3). Listen werden als JSON gespeichert.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from models import (
    ALL_STATUSES,
    Job,
    MatchResult,
    STATUS_NEW,
    STATUS_REJECTED,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    id                  TEXT PRIMARY KEY,
    title               TEXT,
    company             TEXT,
    location            TEXT,
    link                TEXT,
    description         TEXT,
    score               INTEGER,
    recommendation      TEXT,
    positive_reasons    TEXT,
    negative_reasons    TEXT,
    skills_to_emphasize TEXT,
    cover_letter_hint   TEXT,
    status              TEXT DEFAULT 'neu',
    updated_at          TEXT
);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- Schreiben ---------------------------------------------------------
    def upsert_match(self, result: MatchResult) -> None:
        """Speichert/aktualisiert ein Match. Vorhandener Status bleibt erhalten."""
        job = result.job
        existing = self.get_status(job.id)
        status = existing if existing is not None else STATUS_NEW
        self.conn.execute(
            """
            INSERT INTO matches (
                id, title, company, location, link, description,
                score, recommendation, positive_reasons, negative_reasons,
                skills_to_emphasize, cover_letter_hint, status, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,
                company=excluded.company,
                location=excluded.location,
                link=excluded.link,
                description=excluded.description,
                score=excluded.score,
                recommendation=excluded.recommendation,
                positive_reasons=excluded.positive_reasons,
                negative_reasons=excluded.negative_reasons,
                skills_to_emphasize=excluded.skills_to_emphasize,
                cover_letter_hint=excluded.cover_letter_hint,
                updated_at=excluded.updated_at
            """,
            (
                job.id, job.title, job.company, job.location, job.link, job.description,
                result.score, result.recommendation,
                json.dumps(result.positive_reasons, ensure_ascii=False),
                json.dumps(result.negative_reasons, ensure_ascii=False),
                json.dumps(result.skills_to_emphasize, ensure_ascii=False),
                result.cover_letter_hint, status,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        self.conn.commit()

    def save_all(self, results: List[MatchResult]) -> None:
        for r in results:
            self.upsert_match(r)

    def set_status(self, job_id: str, status: str) -> bool:
        if status not in ALL_STATUSES:
            raise ValueError(f"Unbekannter Status: {status}")
        cur = self.conn.execute(
            "UPDATE matches SET status=?, updated_at=? WHERE id=?",
            (status, datetime.now().isoformat(timespec="seconds"), job_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    # -- Lesen -------------------------------------------------------------
    def get_status(self, job_id: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT status FROM matches WHERE id=?", (job_id,)
        ).fetchone()
        return row["status"] if row else None

    def get(self, job_id: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM matches WHERE id=?", (job_id,)
        ).fetchone()

    def get_all(self) -> List[sqlite3.Row]:
        return list(
            self.conn.execute("SELECT * FROM matches ORDER BY score DESC").fetchall()
        )

    def get_top(self, limit: int = 10, min_score: int = 0,
                exclude_rejected: bool = True) -> List[sqlite3.Row]:
        query = "SELECT * FROM matches WHERE score >= ?"
        params: list = [min_score]
        if exclude_rejected:
            query += " AND status != ?"
            params.append(STATUS_REJECTED)
        query += " ORDER BY score DESC LIMIT ?"
        params.append(limit)
        return list(self.conn.execute(query, params).fetchall())

    def status_counts(self) -> dict:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM matches GROUP BY status"
        ).fetchall()
        counts = {s: 0 for s in ALL_STATUSES}
        for row in rows:
            counts[row["status"]] = row["n"]
        return counts


def row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        title=row["title"],
        company=row["company"],
        location=row["location"],
        link=row["link"],
        description=row["description"],
    )
