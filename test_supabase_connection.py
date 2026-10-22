"""Offline checks for the strict Supabase/PostgreSQL connection logic.

Run with:  python test_supabase_connection.py
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import database
from database import (
    Database,
    database_url,
    normalize_database_url,
    postgres_configured,
)

PASSED = []
FAILED = []


def check(name, condition, detail=""):
    if condition:
        PASSED.append(name)
        print(f"  [PASS] {name}")
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name} {detail}")


def clear_env():
    for key in list(os.environ):
        if key.startswith(("SUPABASE_", "DB_", "DATABASE")):
            os.environ.pop(key, None)


def test_url_normalisation():
    print("\n== URL normalisation ==")

    raw = "postgresql://postgres:dummy@pw@db.example.supabase.co:5432/postgres?sslmode=require"
    out = normalize_database_url(raw)
    check(
        "raw '@' in password is percent-encoded",
        "dummy%40pw" in out,
        out,
    )
    check(
        "host is not corrupted by the '@'",
        "@db.example.supabase.co:5432" in out,
        out,
    )

    already = "postgresql://postgres:dummy%40pw@db.example.supabase.co:5432/postgres?sslmode=require"
    check(
        "already-encoded URL is left intact",
        normalize_database_url(already) == already,
        normalize_database_url(already),
    )

    simple = "postgresql://user:pw@host:5432/db"
    check(
        "simple URL round-trips",
        normalize_database_url(simple) == simple,
        normalize_database_url(simple),
    )

    check("empty URL stays empty", normalize_database_url("") == "")


def test_database_url_source():
    print("\n== DATABASE_URL resolution ==")
    clear_env()
    check("no env -> no URL", database_url() == "")
    check("no env -> not configured", postgres_configured() is False)

    os.environ["DATABASE_URL"] = "postgresql://u:p@h:5432/db?sslmode=require"
    check("DATABASE_URL is picked up", postgres_configured() is True)
    check(
        "DATABASE_URL value is returned",
        database_url() == "postgresql://u:p@h:5432/db?sslmode=require",
        database_url(),
    )
    clear_env()


def test_strict_no_sqlite_fallback():
    print("\n== Strict backend selection ==")
    clear_env()

    # 1. No DATABASE_URL -> must raise, never silently use SQLite.
    raised = False
    try:
        Database()
    except RuntimeError as exc:
        raised = True
        check(
            "missing DATABASE_URL raises RuntimeError",
            "DATABASE_URL" in str(exc),
            str(exc),
        )
    if not raised:
        check("missing DATABASE_URL raises RuntimeError", False, "no exception raised")

    # 2. A configured URL must select the postgresql backend.
    os.environ["DATABASE_URL"] = "postgresql://u:p@h:5432/db?sslmode=require"
    db = Database()
    check("configured URL selects postgresql", db.backend == "postgresql", db.backend)

    # 3. SQLite is only used when explicitly requested.
    with tempfile.TemporaryDirectory() as tmp:
        db2 = Database(Path(tmp) / "explicit.db", use_sqlite=True)
        check("explicit use_sqlite selects sqlite", db2.backend == "sqlite", db2.backend)
    clear_env()


def test_connection_uses_psycopg2():
    print("\n== Connection path ==")
    clear_env()
    os.environ["DATABASE_URL"] = (
        "postgresql://postgres:dummy@pw@db.example.supabase.co:5432/postgres?sslmode=require"
    )

    captured = {}

    class FakeCursor:
        def execute(self, sql, params=None):
            captured["sql"] = sql
            captured["params"] = params

        def fetchone(self):
            return {"current_database": "postgres", "current_user": "postgres", "version": "PostgreSQL 16.2 on x86_64"}

        def close(self):
            pass

    class FakeConn:
        def cursor(self, *a, **k):
            return FakeCursor()

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    def fake_connect(dsn, **kwargs):
        captured["dsn"] = dsn
        captured["kwargs"] = kwargs
        return FakeConn()

    original = database.psycopg2.connect
    database.psycopg2.connect = fake_connect
    try:
        db = Database()
        info = db.connect()
    finally:
        database.psycopg2.connect = original

    check(
        "psycopg2.connect receives a single DSN string",
        isinstance(captured.get("dsn"), str),
        str(captured.get("dsn")),
    )
    check(
        "password is encoded in the DSN",
        "dummy%40pw" in captured.get("dsn", ""),
        captured.get("dsn", ""),
    )
    check(
        "host is correct in the DSN",
        "@db.example.supabase.co:5432" in captured.get("dsn", ""),
        captured.get("dsn", ""),
    )
    check(
        "connect_timeout is passed",
        "connect_timeout" in captured.get("kwargs", {}),
        str(captured.get("kwargs")),
    )
    check(
        "connect() returns a description",
        "postgres" in info,
        info,
    )
    clear_env()


def main():
    print("SomaiyaSat - Supabase/PostgreSQL connection tests")
    test_url_normalisation()
    test_database_url_source()
    test_strict_no_sqlite_fallback()
    test_connection_uses_psycopg2()

    total = len(PASSED) + len(FAILED)
    print(f"\n{len(PASSED)}/{total} checks passed")
    if FAILED:
        print("FAILED:")
        for name in FAILED:
            print(f"  - {name}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
