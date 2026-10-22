"""
Validates the PostgreSQL (Supabase) code path without a live server.

A fake DB-API connection records every statement the Database class issues
while ``backend == "postgresql"``. The checks confirm that:

* the ``%s`` placeholder style is used (never SQLite's ``?``);
* every statement is parameterised rather than string-interpolated;
* the PostgreSQL DDL is emitted for both tables;
* ``RETURNING packet_id`` is used for inserts;
* the allow-list protects the ORDER BY clause.

Run with:  python pg_path_test.py
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from database import Database  # noqa: E402


class FakeCursor:
    """Records statements and returns scripted results."""

    def __init__(self, log, results):
        self._log = log
        self._results = results
        self.rowcount = 0
        self.lastrowid = 0

    def execute(self, sql, params=()):
        self._log.append((sql, params))
        self.rowcount = 1

    def fetchall(self):
        return self._results.pop(0) if self._results else []

    def fetchone(self):
        rows = self.fetchall()
        return rows[0] if rows else None

    def close(self):
        pass


class FakeConnection:
    def __init__(self, log, results):
        self._log = log
        self._results = results

    def cursor(self, cursor_factory=None):
        return FakeCursor(self._log, self._results)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def make_db(log, results):
    """Build a Database instance pinned to the PostgreSQL backend."""
    db = Database.__new__(Database)  # bypass __init__ so we can force the backend
    db.backend = "postgresql"
    db.db_path = Path("unused")
    db.last_error = None
    db._local = threading.local()
    db._connect = lambda: FakeConnection(log, results)  # type: ignore[assignment]
    return db


def run() -> int:
    failures = []

    def check(label, condition, extra=""):
        print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" -- {extra}" if extra else ""))
        if not condition:
            failures.append(label)

    print("=" * 62)
    print("PostgreSQL (Supabase) code-path validation")
    print("=" * 62)

    log: list = []
    results: list = []
    db = make_db(log, results)

    # ---------------- schema creation ----------------
    # Only statements that are actually FETCHED consume a scripted result.
    # CREATE TABLE / CREATE INDEX / plain INSERTs use execute() only.
    results.append([{"total": 0}])  # SELECT COUNT(*) FROM telemetry_packets
    for i in range(8):  # 8 seed telemetry inserts (RETURNING packet_id)
        results.append([{"packet_id": i + 1}])

    db.init_db()

    statements = [sql for sql, _ in log]
    joined = "\n".join(statements)

    check(
        "creates the satellites table",
        any("CREATE TABLE IF NOT EXISTS satellites" in s for s in statements),
    )
    check(
        "creates the telemetry_packets table",
        any("CREATE TABLE IF NOT EXISTS telemetry_packets" in s for s in statements),
    )
    check(
        "telemetry_packets uses SERIAL (PostgreSQL auto-increment)",
        "SERIAL PRIMARY KEY" in joined,
    )
    check(
        "telemetry_packets has a FOREIGN KEY to satellites",
        "REFERENCES satellites(satellite_id)" in joined,
    )
    check(
        "inserts use RETURNING packet_id",
        any("RETURNING packet_id" in s for s in statements),
    )
    check(
        "no SQLite-style '?' placeholders leak into the PG path",
        "?" not in joined,
        "found a '?' in the generated SQL" if "?" in joined else "",
    )
    check(
        "the 3 default satellites are seeded",
        sum(1 for s, _ in log if "INSERT INTO satellites" in s) == 3,
    )
    check(
        "the 8 default telemetry packets are seeded",
        sum(1 for s, _ in log if "INSERT INTO telemetry_packets" in s) == 8,
    )

    # ---------------- parameterisation of user data ----------------
    log.clear()
    results.clear()
    results.append([])  # MAX(satellite_id) lookup
    results.append([])  # INSERT satellites
    db.add_satellite("Evil'; DROP TABLE satellites;--", "LEO", 2026, "Active")

    sat_sql, sat_params = log[-1]
    check(
        "satellite name is passed as a bound parameter, not interpolated",
        "DROP TABLE" not in sat_sql,
        sat_sql.strip()[:60],
    )
    check(
        "the malicious name arrives as a parameter value",
        any("DROP TABLE" in str(p) for p in sat_params),
    )

    # ---------------- list_telemetry SQL ----------------
    log.clear()
    results.clear()
    results.append([])
    db.list_telemetry(satellite_id=2, payload_mode="M17", status_like="beacon", limit=50)

    tel_sql, tel_params = log[-1]
    check("list_telemetry joins the satellites table", "JOIN satellites s" in tel_sql)
    check("list_telemetry filters by satellite_id", "t.satellite_id = %s" in tel_sql)
    check("list_telemetry filters by payload_mode", "t.payload_mode = %s" in tel_sql)
    check("list_telemetry filters with LIKE", "t.status_msg LIKE %s" in tel_sql)
    check(
        "list_telemetry passes the right params",
        tel_params == (2, "M17", "%beacon%"),
        str(tel_params),
    )
    check("the LIMIT is an integer, not a parameter", tel_sql.rstrip().endswith("LIMIT 50"))

    # ---------------- ORDER BY allow-list ----------------
    log.clear()
    results.clear()
    results.append([])  # the list_telemetry SELECT
    db.list_telemetry(order_by="packet_id; DROP TABLE satellites", order_dir="asc")
    bad_sql = log[-1][0]
    check(
        "a malicious order_by is replaced by a safe column",
        "DROP TABLE" not in bad_sql,
        bad_sql.strip()[-60:],
    )
    check(
        "a malicious order_by falls back to the default column",
        "ORDER BY t.logged_at ASC" in bad_sql,
        bad_sql.strip()[-40:],
    )

    # A genuinely valid column must still be honoured.
    log.clear()
    results.clear()
    results.append([])  # the list_telemetry SELECT
    db.list_telemetry(order_by="packet_id", order_dir="asc")
    check("a valid order_by is honoured", "ORDER BY t.packet_id ASC" in log[-1][0])

    log.clear()
    results.clear()
    results.append([])  # the list_telemetry SELECT
    db.list_telemetry(order_by="logged_at", order_dir="desc")
    check("desc direction is applied", "ORDER BY t.logged_at DESC" in log[-1][0])

    # ---------------- stats ----------------
    log.clear()
    results.clear()
    # telemetry_stats runs 7 SELECTs in this order:
    #   COUNT packets, COUNT satellites, COUNT active, AVG dbm,
    #   mode breakdown, per satellite, recent
    results.append([{"total_packets": 42}])
    results.append([{"total": 3}])
    results.append([{"total": 1}])
    results.append([{"avg_dbm": -83.5}])
    results.append([{"payload_mode": "M17", "packet_count": 3, "avg_dbm": -79.6}])
    results.append([{"satellite_id": 1, "satellite_name": "SomaiyaSat",
                     "orbit_type": "LEO", "status": "Active", "packet_count": 5}])
    results.append([{"packet_id": 1, "payload_mode": "M17",
                     "signal_strength_dbm": -78.4, "status_msg": "ok",
                     "logged_at": "2026-01-01 00:00:00", "satellite_name": "SomaiyaSat"}])

    stats = db.telemetry_stats()
    check(
        "telemetry_stats returns the expected keys",
        {"total_packets", "total_satellites", "active_satellites", "avg_dbm",
         "mode_breakdown", "per_satellite", "recent"} <= set(stats.keys()),
    )
    check(
        "telemetry_stats counts packets",
        stats["total_packets"] == 42,
        str(stats["total_packets"]),
    )
    check(
        "telemetry_stats uses COUNT(*) on both tables",
        any("COUNT(*) AS total_packets" in s for s, _ in log),
    )

    # ---------------- placeholder property ----------------
    check("the placeholder token for PostgreSQL is %s", db._ph == "%s")

    print("=" * 62)
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ALL POSTGRESQL PATH CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
