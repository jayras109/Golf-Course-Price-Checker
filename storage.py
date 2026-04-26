import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

_DB = Path(__file__).parent / "alerts.db"


def _conn():
    db = sqlite3.connect(_DB)
    db.execute("""
        CREATE TABLE IF NOT EXISTS alerted (
            id        TEXT NOT NULL,
            alerted_at TEXT NOT NULL,
            PRIMARY KEY (id, alerted_at)
        )
    """)
    db.commit()
    return db


def is_new(tee_time_id: str) -> bool:
    """True if we have NOT sent an alert for this tee time today."""
    today = datetime.now().date().isoformat()
    with _conn() as db:
        row = db.execute(
            "SELECT 1 FROM alerted WHERE id = ? AND alerted_at >= ?",
            (tee_time_id, today),
        ).fetchone()
    return row is None


def mark_alerted(tee_time_ids: list):
    now = datetime.now().isoformat()
    with _conn() as db:
        db.executemany(
            "INSERT OR IGNORE INTO alerted (id, alerted_at) VALUES (?, ?)",
            [(tid, now) for tid in tee_time_ids],
        )
        db.commit()


def cleanup_old(days: int = 14):
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    with _conn() as db:
        db.execute("DELETE FROM alerted WHERE alerted_at < ?", (cutoff,))
        db.commit()
