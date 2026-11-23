"""SQLite state: proposals, decisions, run history and an audit log of every CRM write.

Re-run safety lives here: proposals are keyed by fingerprint, and a fingerprint that was ever
approved or rejected is never put back in the queue.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import config

PENDING, APPROVED, REJECTED, SUPERSEDED, FAILED, CONFLICT, APPLYING = (
    "pending", "approved", "rejected", "superseded", "failed", "conflict", "applying")
DECIDED = (APPROVED, REJECTED)

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT,
    locations INTEGER, accounts INTEGER,
    new_proposals INTEGER, refreshed INTEGER, skipped_decided INTEGER, superseded INTEGER,
    warnings TEXT, coverage TEXT
);
CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    account_id TEXT,
    location_slug TEXT,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    first_seen_run INTEGER, last_seen_run INTEGER,
    decided_at TEXT, decided_by TEXT, reviewer_comment TEXT,
    result TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    proposal_id INTEGER,
    action TEXT NOT NULL,
    detail TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path or config.DB_PATH)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    # ---------- runs ----------
    def start_run(self) -> int:
        cur = self.db.execute("INSERT INTO runs (started_at, status) VALUES (?, 'running')", (now(),))
        self.db.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, **stats) -> None:
        stats = {k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in stats.items()}
        sets = ", ".join(f"{k} = ?" for k in stats)
        self.db.execute(f"UPDATE runs SET finished_at = ?, {sets} WHERE id = ?", (now(), *stats.values(), run_id))
        self.db.commit()

    def last_run(self) -> dict | None:
        row = self.db.execute("SELECT * FROM runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return None
        d = dict(row)
        d["warnings"] = json.loads(d["warnings"] or "[]")
        d["coverage"] = json.loads(d["coverage"] or "[]")
        return d

    # ---------- proposals ----------
    def sync_proposals(self, run_id: int, proposals: list) -> dict:
        """Insert new proposals, refresh pending ones, skip decided ones, supersede pending ones that vanished."""
        stats = {"new_proposals": 0, "refreshed": 0, "skipped_decided": 0, "superseded": 0}
        seen = set()
        for p in proposals:
            d = p.to_dict() if hasattr(p, "to_dict") else p
            fp = d["fingerprint"]
            seen.add(fp)
            row = self.db.execute("SELECT id, status FROM proposals WHERE fingerprint = ?", (fp,)).fetchone()
            if row is None:
                self.db.execute(
                    "INSERT INTO proposals (fingerprint, kind, title, account_id, location_slug, payload, status, "
                    "first_seen_run, last_seen_run) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                    (fp, d["kind"], d["title"], d.get("account_id"), d.get("location_slug"), json.dumps(d), run_id, run_id))
                stats["new_proposals"] += 1
            elif row["status"] in DECIDED or row["status"] == APPLYING:
                self.db.execute("UPDATE proposals SET last_seen_run = ? WHERE id = ?", (run_id, row["id"]))
                stats["skipped_decided"] += 1
            else:  # pending / superseded / failed / conflict -> back in the queue with fresh evidence
                self.db.execute("UPDATE proposals SET payload = ?, title = ?, status = 'pending', last_seen_run = ? "
                                "WHERE id = ?", (json.dumps(d), d["title"], run_id, row["id"]))
                stats["refreshed"] += 1
        for row in self.db.execute("SELECT id, fingerprint FROM proposals WHERE status IN (?, ?, ?)",
                                   (PENDING, FAILED, CONFLICT)).fetchall():
            if row["fingerprint"] not in seen:
                self.db.execute("UPDATE proposals SET status = ? WHERE id = ?", (SUPERSEDED, row["id"]))
                self.log(row["id"], "superseded", {"run_id": run_id, "reason": "no longer proposed by the pipeline"})
                stats["superseded"] += 1
        self.db.commit()
        return stats

    def list(self, statuses: tuple[str, ...]) -> list[dict]:
        q = f"SELECT * FROM proposals WHERE status IN ({','.join('?' * len(statuses))}) ORDER BY id"
        return [self._row(r) for r in self.db.execute(q, statuses).fetchall()]

    def get(self, pid: int) -> dict | None:
        r = self.db.execute("SELECT * FROM proposals WHERE id = ?", (pid,)).fetchone()
        return self._row(r) if r else None

    def claim(self, pid: int) -> bool:
        """Atomically move pending -> applying so a double click can never apply twice."""
        cur = self.db.execute("UPDATE proposals SET status = ? WHERE id = ? AND status IN (?, ?)",
                              (APPLYING, pid, PENDING, FAILED))
        self.db.commit()
        return cur.rowcount == 1

    def decide(self, pid: int, status: str, reviewer: str, comment: str = "", result: dict | None = None) -> None:
        self.db.execute("UPDATE proposals SET status = ?, decided_at = ?, decided_by = ?, reviewer_comment = ?, "
                        "result = ? WHERE id = ?", (status, now(), reviewer, comment, json.dumps(result or {}), pid))
        self.db.commit()

    def set_status(self, pid: int, status: str, result: dict | None = None) -> None:
        self.db.execute("UPDATE proposals SET status = ?, result = ? WHERE id = ?", (status, json.dumps(result or {}), pid))
        self.db.commit()

    def counts(self) -> dict:
        return {r["status"]: r["n"] for r in self.db.execute("SELECT status, COUNT(*) n FROM proposals GROUP BY status")}

    # ---------- audit ----------
    def log(self, pid: int | None, action: str, detail: dict) -> None:
        self.db.execute("INSERT INTO audit_log (ts, proposal_id, action, detail) VALUES (?, ?, ?, ?)",
                        (now(), pid, action, json.dumps(detail, default=str)))
        self.db.commit()

    def audit(self, limit: int = 200) -> list[dict]:
        rows = self.db.execute("SELECT a.*, p.title FROM audit_log a LEFT JOIN proposals p ON p.id = a.proposal_id "
                               "ORDER BY a.id DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"] or "{}")} for r in rows]

    @staticmethod
    def _row(r) -> dict:
        d = dict(r)
        d["payload"] = json.loads(d["payload"])
        d["result"] = json.loads(d["result"]) if d.get("result") else {}
        return d
