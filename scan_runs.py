"""Durable strategy-run tracking using the configured SQLite/Turso backend."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from database import connect


SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_runs (
    run_id TEXT PRIMARY KEY,
    system TEXT NOT NULL,
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT,
    status TEXT NOT NULL CHECK(status IN ('RUNNING', 'SUCCEEDED', 'FAILED')),
    result_count INTEGER,
    error TEXT
)
"""


def initialize(conn) -> None:
    conn.execute(SCHEMA)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_scan_runs_system_started "
        "ON scan_runs(system, started_at_utc DESC)"
    )
    conn.commit()


def start_run(system: str) -> str:
    run_id = uuid4().hex
    conn = connect()
    try:
        initialize(conn)
        conn.execute(
            "INSERT INTO scan_runs (run_id, system, started_at_utc, status) "
            "VALUES (?, ?, ?, 'RUNNING')",
            (run_id, system, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return run_id
    finally:
        conn.close()


def finish_run(run_id: str, result_count: int) -> None:
    _complete(run_id, "SUCCEEDED", result_count, None)


def fail_run(run_id: str, error: str) -> None:
    _complete(run_id, "FAILED", None, error[:4000])


def _complete(run_id: str, status: str, result_count: int | None, error: str | None) -> None:
    conn = connect()
    try:
        initialize(conn)
        conn.execute(
            """UPDATE scan_runs
                  SET completed_at_utc = ?, status = ?, result_count = ?, error = ?
                WHERE run_id = ?""",
            (datetime.now(timezone.utc).isoformat(), status, result_count, error, run_id),
        )
        conn.commit()
    finally:
        conn.close()


def latest_runs() -> dict[str, dict[str, object]]:
    conn = connect()
    try:
        initialize(conn)
        rows = conn.execute(
            """SELECT run_id, system, started_at_utc, completed_at_utc,
                      status, result_count, error
                 FROM scan_runs ORDER BY started_at_utc DESC"""
        ).fetchall()
        columns = ["run_id", "system", "started_at_utc", "completed_at_utc",
                   "status", "result_count", "error"]
        latest: dict[str, dict[str, object]] = {}
        for values in rows:
            row = dict(zip(columns, values))
            latest.setdefault(str(row["system"]), row)
        return latest
    finally:
        conn.close()

