"""
SQLite database operations for floop-bench results.

Uses WAL mode for safe concurrent writes from parallel workers.
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

from agents.base import RunResult

DB_PATH = Path("results/results.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    instance_id TEXT NOT NULL,
    arm TEXT NOT NULL,
    model TEXT NOT NULL,
    floop_enabled BOOLEAN NOT NULL,
    model_patch TEXT,
    resolved BOOLEAN,
    status TEXT NOT NULL,
    duration_seconds REAL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    transcript_path TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (instance_id, arm)
);
"""


@contextlib.contextmanager
def _connect(db_path: Path | None = None):
    """Open a SQLite connection, yield it, and close on exit."""
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: Path | None = None) -> None:
    """Create database and tables if they don't exist."""
    with _connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def save_run(result: RunResult, db_path: Path | None = None) -> None:
    """Save a run result to the database. Upserts on (instance_id, arm)."""
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO runs
            (instance_id, arm, model, floop_enabled, model_patch, resolved,
             status, duration_seconds, input_tokens, output_tokens, cost_usd,
             transcript_path, error_message)
            VALUES (
                ?, ?, ?, ?, ?,
                (SELECT resolved FROM runs WHERE instance_id = ? AND arm = ?),
                ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT(instance_id, arm) DO UPDATE SET
                model = excluded.model,
                floop_enabled = excluded.floop_enabled,
                model_patch = excluded.model_patch,
                resolved = COALESCE(runs.resolved, excluded.resolved),
                status = excluded.status,
                duration_seconds = excluded.duration_seconds,
                input_tokens = excluded.input_tokens,
                output_tokens = excluded.output_tokens,
                cost_usd = excluded.cost_usd,
                transcript_path = excluded.transcript_path,
                error_message = excluded.error_message
            """,
            (
                result.instance_id,
                result.arm,
                result.model,
                result.floop_enabled,
                result.model_patch,
                result.instance_id,
                result.arm,
                result.status,
                result.duration_seconds,
                result.input_tokens,
                result.output_tokens,
                result.cost_usd,
                result.transcript_path,
                result.error_message,
            ),
        )
        conn.commit()


def load_completed(db_path: Path | None = None) -> set[tuple[str, str]]:
    """Load set of (instance_id, arm) pairs that have completed or errored runs."""
    with _connect(db_path) as conn:
        try:
            rows = conn.execute(
                "SELECT instance_id, arm FROM runs "
                "WHERE status IN ('completed', 'timeout', 'error')"
            ).fetchall()
            return {(row["instance_id"], row["arm"]) for row in rows}
        except sqlite3.OperationalError:
            return set()


def get_total_cost(db_path: Path | None = None) -> float:
    """Get total cost across all runs."""
    with _connect(db_path) as conn:
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) as total FROM runs"
            ).fetchone()
            return row["total"]
        except sqlite3.OperationalError:
            return 0.0


def update_resolved(
    instance_id: str, arm: str, resolved: bool, db_path: Path | None = None
) -> None:
    """Update the resolved status for a run after SWE-bench evaluation."""
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE runs SET resolved = ? WHERE instance_id = ? AND arm = ?",
            (resolved, instance_id, arm),
        )
        conn.commit()


def get_runs(arm: str | None = None, db_path: Path | None = None) -> list[dict]:
    """Get all runs, optionally filtered by arm."""
    with _connect(db_path) as conn:
        if arm:
            rows = conn.execute(
                "SELECT * FROM runs WHERE arm = ? ORDER BY created_at", (arm,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY created_at"
            ).fetchall()
        return [dict(row) for row in rows]


def get_arm_stats(db_path: Path | None = None) -> list[dict]:
    """Get summary statistics per arm."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                arm,
                COUNT(*) as total,
                SUM(CASE WHEN resolved = 1 THEN 1 ELSE 0 END) as resolved_count,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                SUM(CASE WHEN status = 'timeout' THEN 1 ELSE 0 END) as timeouts,
                SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as errors,
                AVG(duration_seconds) as avg_duration,
                SUM(cost_usd) as total_cost,
                AVG(cost_usd) as avg_cost
            FROM runs
            GROUP BY arm
            """
        ).fetchall()
        return [dict(row) for row in rows]
