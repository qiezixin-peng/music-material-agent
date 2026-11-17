from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL,
    workbook_path TEXT NOT NULL,
    workbook_sha256 TEXT NOT NULL,
    sheet_name TEXT NOT NULL,
    header_row INTEGER NOT NULL,
    spec_json TEXT NOT NULL,
    trace_dir TEXT NOT NULL,
    output_dir TEXT NOT NULL,
    row_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS job_rows (
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    row_id TEXT NOT NULL,
    row_number INTEGER NOT NULL,
    data_json TEXT NOT NULL,
    PRIMARY KEY (job_id, row_id),
    UNIQUE (job_id, row_number)
);

CREATE TABLE IF NOT EXISTS partitions (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    status TEXT NOT NULL,
    claimed_by TEXT,
    lease_until TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    snapshot_hash TEXT,
    sealed_at TEXT,
    UNIQUE (job_id, platform)
);

CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    row_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    collector TEXT NOT NULL,
    status TEXT NOT NULL,
    data_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    UNIQUE (job_id, row_id, platform),
    FOREIGN KEY (job_id, row_id) REFERENCES job_rows(job_id, row_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reviews (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    source_platform TEXT NOT NULL,
    reviewer_platform TEXT NOT NULL,
    status TEXT NOT NULL,
    claimed_by TEXT,
    lease_until TEXT,
    snapshot_hash TEXT NOT NULL,
    sealed_at TEXT,
    UNIQUE (job_id, source_platform, reviewer_platform)
);

CREATE TABLE IF NOT EXISTS review_verdicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id TEXT NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    row_id TEXT NOT NULL,
    verdict TEXT NOT NULL,
    patch_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    UNIQUE (review_id, row_id)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT,
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rows_job ON job_rows(job_id, row_number);
CREATE INDEX IF NOT EXISTS idx_observations_job_platform ON observations(job_id, platform);
CREATE INDEX IF NOT EXISTS idx_reviews_job ON reviews(job_id, status);
CREATE INDEX IF NOT EXISTS idx_verdicts_review ON review_verdicts(review_id);
CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id, id);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def transaction(self, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
