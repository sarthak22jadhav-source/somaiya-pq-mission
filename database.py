"""
SomaiyaSat & SomaiyaPod - PocketQube Mission
Database layer (Supabase / PostgreSQL via psycopg2).

The dashboard is wired to a Supabase-hosted PostgreSQL database and connects
through ``DATABASE_URL`` using psycopg2. There is deliberately **no automatic
SQLite fallback**: if ``DATABASE_URL`` is missing or the connection fails, the
application stops at startup with a clear error. A silent fallback would write
mission data to a local file while the Supabase tables stayed empty, which is
exactly the confusing state this module is meant to prevent.

The only supported exception is an explicit ``Database(use_sqlite=True)``,
which the offline test-suite uses to exercise the routes without a network.

Schema
------
satellites
    satellite_id        INT  PRIMARY KEY
    satellite_name      TEXT NOT NULL
    orbit_type          TEXT
    launch_year         INT
    status              TEXT

telemetry_packets
    packet_id           SERIAL PRIMARY KEY
    satellite_id        INT  REFERENCES satellites(satellite_id)
    payload_mode        TEXT
    signal_strength_dbm DECIMAL(6,2)
    status_msg          TEXT
    logged_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import quote, unquote

try:  # pragma: no cover - depends on the local environment
    import psycopg2
    from psycopg2.extras import RealDictCursor

    PSYCOPG2_AVAILABLE = True
except Exception:  # pragma: no cover
    psycopg2 = None  # type: ignore[assignment]
    RealDictCursor = None  # type: ignore[assignment]
    PSYCOPG2_AVAILABLE = False


BASE_DIR = Path(__file__).resolve().parent
SQLITE_PATH = BASE_DIR / "somaiya_pocketqube.db"

# Seconds to wait for the Supabase TCP/TLS handshake before giving up.
CONNECT_TIMEOUT = int(os.getenv("DB_CONNECT_TIMEOUT", "10"))

PAYLOAD_MODES = ("M17", "Codec2", "SSTV", "TT&C")
ORBIT_TYPES = ("LEO", "MEO", "GEO", "SSO", "PO")
SATELLITE_STATUSES = ("Active", "Commissioning", "Standby", "Retired")


# --------------------------------------------------------------------------- #
# Environment / .env handling
# --------------------------------------------------------------------------- #
def load_dotenv(path: Optional[Path] = None) -> Dict[str, str]:
    """Minimal, dependency-free ``.env`` loader (stdlib only).

    Existing environment variables always win over the file contents.
    """
    env_path = Path(path) if path else BASE_DIR / ".env"
    loaded: Dict[str, str] = {}
    if not env_path.is_file():
        return loaded

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded


# Load the local .env once, at import time, so that the database layer behaves
# identically whether it is reached through ``app.py`` or imported directly.
load_dotenv(BASE_DIR / ".env")


def _pg_config() -> Dict[str, Any]:
    """Build the libpq connection parameters for Supabase from the env."""
    return {
        "host": os.getenv("SUPABASE_HOST", os.getenv("DB_HOST", "")),
        "port": os.getenv("SUPABASE_PORT", os.getenv("DB_PORT", "5432")),
        "dbname": os.getenv("SUPABASE_DB", os.getenv("DB_NAME", "postgres")),
        "user": os.getenv("SUPABASE_USER", os.getenv("DB_USER", "")),
        "password": os.getenv("SUPABASE_PASSWORD", os.getenv("DB_PASSWORD", "")),
        "sslmode": os.getenv("SUPABASE_SSLMODE", "require"),
        "connect_timeout": int(os.getenv("DB_CONNECT_TIMEOUT", "10")),
    }


def _split_authority(authority: str) -> tuple:
    """Split ``user:password@host:port`` into its parts.

    The password may itself contain unencoded "@" characters, so the *last*
    "@" in the authority is treated as the userinfo separator.
    """
    userinfo, _, hostpart = authority.rpartition("@")
    if not userinfo:
        return "", "", hostpart
    user, _, password = userinfo.partition(":")
    return user, password, hostpart


def normalize_database_url(url: str) -> str:
    """Normalise a PostgreSQL URL so psycopg2 can always parse it.

    Supabase passwords frequently contain characters that are reserved in a
    URI (``@`` most notably). A literal "@" in the password makes libpq
    treat the remainder of the string as the host, which fails with a
    confusing "could not translate host name" error. This function
    percent-encodes the userinfo section so the URL always parses the same
    way, while leaving an already-encoded URL untouched.
    """
    url = (url or "").strip()
    if not url:
        return ""

    scheme, sep, rest = url.partition("://")
    if not sep:
        return url

    authority, slash, tail = rest.partition("/")
    query = ""
    if slash:
        tail, _, query = tail.partition("?")

    user, password, hostpart = _split_authority(authority)
    if not user:
        # No credentials in the URL - nothing to normalise.
        return url

    # Only re-encode characters that are actually illegal in userinfo.
    user = quote(unquote(user), safe="")
    password = quote(unquote(password), safe="")
    hostpart = quote(unquote(hostpart), safe=":[]")

    rebuilt = f"{scheme}://{user}:{password}@{hostpart}"
    if slash:
        rebuilt += "/" + tail
    if query:
        rebuilt += "?" + query
    return rebuilt


def database_url() -> str:
    """Return the Supabase connection URL.

    ``DATABASE_URL`` is the canonical source. When it is absent the URL is
    composed from the discrete ``SUPABASE_*`` variables so that both styles of
    configuration end up going through exactly the same code path.
    """
    url = (os.getenv("DATABASE_URL") or "").strip()
    if url:
        return normalize_database_url(url)

    cfg = _pg_config()
    if not (cfg["host"] and cfg["user"] and cfg["password"]):
        return ""

    # The password must be percent-encoded: a literal "@" would otherwise be
    # parsed as the host separator and silently break the connection.
    user = quote(str(cfg["user"]), safe="")
    password = quote(str(cfg["password"]), safe="")
    dbname = quote(str(cfg["dbname"]), safe="")
    sslmode = quote(str(cfg["sslmode"]), safe="")
    return (
        f"postgresql://{user}:{password}@{cfg['host']}:{cfg['port']}"
        f"/{dbname}?sslmode={sslmode}"
    )


def postgres_configured() -> bool:
    """True when a Supabase ``DATABASE_URL`` is available to connect with."""
    return bool(database_url())


# --------------------------------------------------------------------------- #
# Seeding data
# --------------------------------------------------------------------------- #
DEFAULT_SATELLITES: Sequence[Dict[str, Any]] = (
    {
        "satellite_id": 1,
        "satellite_name": "SomaiyaSat",
        "orbit_type": "LEO",
        "launch_year": 2024,
        "status": "Active",
    },
    {
        "satellite_id": 2,
        "satellite_name": "SomaiyaPod",
        "orbit_type": "SSO",
        "launch_year": 2025,
        "status": "Commissioning",
    },
    {
        "satellite_id": 3,
        "satellite_name": "SomaiyaPod-ReLAY",
        "orbit_type": "MEO",
        "launch_year": 2026,
        "status": "Standby",
    },
)

# Representative telemetry so the dashboard is meaningful on first launch.
DEFAULT_TELEMETRY: Sequence[Sequence[Any]] = (
    (1, "M17", -78.40, "M17 link locked, frame counter nominal"),
    (1, "Codec2", -84.20, "Codec2 voice burst decoded"),
    (1, "SSTV", -96.75, "SSTV image beacon transmitted"),
    (1, "TT&C", -71.10, "Housekeeping frame acknowledged"),
    (2, "M17", -88.05, "AI route selected: M17 via SomaiyaSat"),
    (2, "Codec2", -91.60, "Voice loopback test complete"),
    (2, "TT&C", -69.85, "Beacon sync acquired"),
    (3, "TT&C", -102.30, "Standby, listening for uplink"),
)


class Database:
    """Thin data-access layer over Supabase (PostgreSQL).

    The dashboard is wired to Supabase through ``DATABASE_URL`` and connects
    with psycopg2. Local SQLite is only used when explicitly requested by the
    caller (``use_sqlite=True``) - it is never an automatic fallback, because
    silently writing mission data to a local file instead of the Supabase
    project makes the tables appear to be "missing" in the Supabase dashboard.
    """

    def __init__(
        self,
        db_path: Path = SQLITE_PATH,
        use_sqlite: bool = False,
    ) -> None:
        self.db_path = Path(db_path)
        self._local = threading.local()
        self.last_error: Optional[str] = None
        self.connection_info: str = ""

        if use_sqlite:
            self.backend = "sqlite"
        else:
            # Strict: Supabase is the only supported production backend.
            if not PSYCOPG2_AVAILABLE:
                raise RuntimeError(
                    "psycopg2 is required to connect to Supabase but is not "
                    "installed. Run: pip install psycopg2-binary"
                )
            if not postgres_configured():
                raise RuntimeError(
                    "DATABASE_URL is not set - refusing to fall back to a local "
                    "SQLite file. Set DATABASE_URL in your .env (see .env.example) "
                    "or start the app with DATABASE_URL=... in the environment."
                )
            self.backend = "postgresql"

    @property
    def _ph(self) -> str:
        """The placeholder token for the active backend."""
        return "%s" if self.backend == "postgresql" else "?"

    # -- connection handling ------------------------------------------------
    def _connect(self):
        """Return a fresh DB-API connection for the configured backend."""
        if self.backend == "postgresql":
            url = database_url()
            if not url:
                raise RuntimeError(
                    "DATABASE_URL is not set - cannot connect to Supabase."
                )
            conn = psycopg2.connect(url, connect_timeout=CONNECT_TIMEOUT)
            return conn
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def connection(self):
        """Return a per-thread connection."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None

    def connect(self) -> str:
        """Open (and verify) the connection, returning a short description.

        Raises on failure so the application fails fast instead of silently
        degrading to a local SQLite file.
        """
        conn = self.connection()
        if self.backend == "postgresql":
            cur = conn.cursor()
            try:
                cur.execute("SELECT current_database(), current_user, version()")
                row = cur.fetchone()
            finally:
                cur.close()
            db_name = row["current_database"] if isinstance(row, dict) else row[0]
            db_user = row["current_user"] if isinstance(row, dict) else row[1]
            version = (row["version"] if isinstance(row, dict) else row[2]) or ""
            pg_version = version.split(" on ")[0]
            self.connection_info = f"{db_user}@{db_name} ({pg_version})"
            return self.connection_info
        self.connection_info = f"sqlite:{self.db_path.name}"
        return self.connection_info

    def _cursor(self):
        conn = self.connection()
        if self.backend == "postgresql":
            return conn, conn.cursor(cursor_factory=RealDictCursor)
        return conn, conn.cursor()

    @staticmethod
    def _as_dict(row) -> Dict[str, Any]:
        if row is None:
            return {}
        if isinstance(row, dict):
            return dict(row)
        if isinstance(row, sqlite3.Row):
            return {k: row[k] for k in row.keys()}
        return dict(row)

    def query(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        conn, cur = self._cursor()
        try:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
            conn.commit()
            return [self._as_dict(r) for r in rows]
        except Exception as exc:
            conn.rollback()
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            try:
                cur.close()
            except Exception:
                pass

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Run a write statement; returns the affected row count."""
        conn, cur = self._cursor()
        try:
            cur.execute(sql, tuple(params))
            conn.commit()
            return cur.rowcount
        except Exception as exc:
            conn.rollback()
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            try:
                cur.close()
            except Exception:
                pass

    # -- schema bootstrap ---------------------------------------------------
    def init_db(self) -> None:
        """Create the tables if they do not exist and seed the default rows."""
        if self.backend == "postgresql":
            self._init_postgres()
        else:
            self._init_sqlite()
        self.seed_defaults()

    def _init_postgres(self) -> None:
        self.execute(
            """
            CREATE TABLE IF NOT EXISTS satellites (
                satellite_id   INT PRIMARY KEY,
                satellite_name TEXT NOT NULL,
                orbit_type     TEXT,
                launch_year    INT,
                status         TEXT
            )
            """
        )
        self.execute(
            """
            CREATE TABLE IF NOT EXISTS telemetry_packets (
                packet_id           SERIAL PRIMARY KEY,
                satellite_id        INT REFERENCES satellites(satellite_id),
                payload_mode        TEXT,
                signal_strength_dbm DECIMAL(6,2),
                status_msg          TEXT,
                logged_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.execute(
            "CREATE INDEX IF NOT EXISTS idx_telemetry_satellite "
            "ON telemetry_packets (satellite_id)"
        )
        self.execute(
            "CREATE INDEX IF NOT EXISTS idx_telemetry_logged_at "
            "ON telemetry_packets (logged_at DESC)"
        )

    def _init_sqlite(self) -> None:
        self.execute(
            """
            CREATE TABLE IF NOT EXISTS satellites (
                satellite_id   INTEGER PRIMARY KEY,
                satellite_name TEXT NOT NULL,
                orbit_type     TEXT,
                launch_year    INTEGER,
                status         TEXT
            )
            """
        )
        self.execute(
            """
            CREATE TABLE IF NOT EXISTS telemetry_packets (
                packet_id           INTEGER PRIMARY KEY AUTOINCREMENT,
                satellite_id        INTEGER REFERENCES satellites(satellite_id),
                payload_mode        TEXT,
                signal_strength_dbm REAL,
                status_msg          TEXT,
                logged_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.execute(
            "CREATE INDEX IF NOT EXISTS idx_telemetry_satellite "
            "ON telemetry_packets (satellite_id)"
        )
        self.execute(
            "CREATE INDEX IF NOT EXISTS idx_telemetry_logged_at "
            "ON telemetry_packets (logged_at DESC)"
        )

    # -- seeding ------------------------------------------------------------
    def seed_defaults(self) -> None:
        """Insert the default SomaiyaSat / SomaiyaPod nodes if they are absent."""
        ph = self._ph
        for sat in DEFAULT_SATELLITES:
            self.execute(
                f"""
                INSERT INTO satellites
                    (satellite_id, satellite_name, orbit_type, launch_year, status)
                VALUES ({ph}, {ph}, {ph}, {ph}, {ph})
                ON CONFLICT (satellite_id) DO NOTHING
                """,
                (
                    sat["satellite_id"],
                    sat["satellite_name"],
                    sat["orbit_type"],
                    sat["launch_year"],
                    sat["status"],
                ),
            )

        existing = self.query_one("SELECT COUNT(*) AS total FROM telemetry_packets")
        if existing and int(existing.get("total", 0)) == 0:
            for sat_id, mode, strength, msg in DEFAULT_TELEMETRY:
                self.add_telemetry_packet(
                    satellite_id=sat_id,
                    payload_mode=mode,
                    signal_strength_dbm=strength,
                    status_msg=msg,
                )

    # -- satellite helpers --------------------------------------------------
    def list_satellites(self) -> List[Dict[str, Any]]:
        return self.query("SELECT * FROM satellites ORDER BY satellite_id ASC")

    def get_satellite(self, satellite_id: int) -> Optional[Dict[str, Any]]:
        return self.query_one(
            f"SELECT * FROM satellites WHERE satellite_id = {self._ph}", (satellite_id,)
        )

    def add_satellite(
        self,
        satellite_name: str,
        orbit_type: str = "LEO",
        launch_year: Optional[int] = None,
        status: str = "Active",
        satellite_id: Optional[int] = None,
    ) -> int:
        """Insert a new satellite node; returns the assigned satellite_id.

        When ``satellite_id`` is omitted the next free id is derived from the
        current maximum. Because that lookup is not atomic, the insert is
        retried on a unique-constraint violation so two concurrent requests
        can never end up with the same id.
        """
        ph = self._ph
        insert_sql = f"""
            INSERT INTO satellites
                (satellite_id, satellite_name, orbit_type, launch_year, status)
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph})
        """

        if satellite_id is not None:
            self.execute(
                insert_sql,
                (satellite_id, satellite_name, orbit_type, launch_year, status),
            )
            return satellite_id

        last_error: Optional[Exception] = None
        for _ in range(5):
            nxt = self.query_one(
                "SELECT COALESCE(MAX(satellite_id), 0) + 1 AS next_id FROM satellites"
            )
            candidate = int((nxt or {}).get("next_id", 1))
            try:
                self.execute(
                    insert_sql,
                    (candidate, satellite_name, orbit_type, launch_year, status),
                )
                return candidate
            except Exception as exc:
                last_error = exc
                if "unique" not in str(exc).lower() and "duplicate" not in str(exc).lower():
                    raise

        raise last_error if last_error else RuntimeError("Could not assign a satellite_id")

    def update_satellite_status(self, satellite_id: int, status: str) -> None:
        ph = self._ph
        self.execute(
            f"UPDATE satellites SET status = {ph} WHERE satellite_id = {ph}",
            (status, satellite_id),
        )

    # -- telemetry helpers --------------------------------------------------
    def add_telemetry_packet(
        self,
        satellite_id: int,
        payload_mode: str,
        signal_strength_dbm: Optional[float],
        status_msg: str = "",
    ) -> Optional[int]:
        """Insert a telemetry packet; returns the new packet_id when available.

        The id is taken from the database itself (``RETURNING`` on PostgreSQL,
        ``lastrowid`` on SQLite) rather than by looking up ``MAX(packet_id)``,
        which would be racy under concurrent writes.
        """
        ph = self._ph
        if self.backend == "postgresql":
            rows = self.query(
                f"""
                INSERT INTO telemetry_packets
                    (satellite_id, payload_mode, signal_strength_dbm, status_msg, logged_at)
                VALUES ({ph}, {ph}, {ph}, {ph}, CURRENT_TIMESTAMP)
                RETURNING packet_id
                """,
                (satellite_id, payload_mode, signal_strength_dbm, status_msg),
            )
            if rows:
                return int(rows[0]["packet_id"])
            return None

        conn, cur = self._cursor()
        try:
            cur.execute(
                """
                INSERT INTO telemetry_packets
                    (satellite_id, payload_mode, signal_strength_dbm, status_msg, logged_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (satellite_id, payload_mode, signal_strength_dbm, status_msg),
            )
            conn.commit()
            return int(cur.lastrowid)
        except Exception as exc:
            conn.rollback()
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            try:
                cur.close()
            except Exception:
                pass

    def list_telemetry(
        self,
        satellite_id: Optional[int] = None,
        payload_mode: Optional[str] = None,
        status_like: Optional[str] = None,
        order_by: str = "logged_at",
        order_dir: str = "desc",
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Fetch joined telemetry rows with WHERE / ORDER BY / LIKE filtering.

        The join mirrors the SQL the dashboard needs::

            SELECT t.*, s.satellite_name, s.orbit_type, s.status
            FROM telemetry_packets t
            JOIN satellites s ON t.satellite_id = s.satellite_id
            WHERE ...
            ORDER BY ...
            LIMIT ...
        """
        ph = self._ph
        allowed_order = {
            "logged_at": "t.logged_at",
            "packet_id": "t.packet_id",
            "satellite_name": "s.satellite_name",
            "payload_mode": "t.payload_mode",
            "signal_strength_dbm": "t.signal_strength_dbm",
        }
        order_col = allowed_order.get(order_by, "t.logged_at")
        direction = "ASC" if str(order_dir).lower() == "asc" else "DESC"

        sql = (
            "SELECT t.packet_id, t.satellite_id, t.payload_mode, "
            "t.signal_strength_dbm, t.status_msg, t.logged_at, "
            "s.satellite_name, s.orbit_type, s.status AS satellite_status "
            "FROM telemetry_packets t "
            "JOIN satellites s ON t.satellite_id = s.satellite_id "
            "WHERE 1 = 1 "
        )
        params: List[Any] = []

        if satellite_id is not None:
            sql += f"AND t.satellite_id = {ph} "
            params.append(satellite_id)
        if payload_mode:
            sql += f"AND t.payload_mode = {ph} "
            params.append(payload_mode)
        if status_like:
            sql += f"AND t.status_msg LIKE {ph} "
            params.append(f"%{status_like}%")

        sql += f"ORDER BY {order_col} {direction} LIMIT {int(limit)}"
        return self.query(sql, params)

    def telemetry_stats(self) -> Dict[str, Any]:
        """Aggregate statistics used by the dashboard summary tiles."""
        ph = self._ph
        totals = self.query_one(
            "SELECT COUNT(*) AS total_packets FROM telemetry_packets"
        ) or {}
        satellites = self.query_one("SELECT COUNT(*) AS total FROM satellites") or {}
        active = self.query_one(
            f"SELECT COUNT(*) AS total FROM satellites WHERE status = {ph}", ("Active",)
        ) or {}
        avg = self.query_one(
            "SELECT AVG(signal_strength_dbm) AS avg_dbm FROM telemetry_packets"
        ) or {}
        modes = self.query(
            "SELECT payload_mode, COUNT(*) AS packet_count, "
            "AVG(signal_strength_dbm) AS avg_dbm "
            "FROM telemetry_packets GROUP BY payload_mode ORDER BY payload_mode"
        )
        per_satellite = self.query(
            "SELECT s.satellite_id, s.satellite_name, s.orbit_type, s.status, "
            "COUNT(t.packet_id) AS packet_count "
            "FROM satellites s "
            "LEFT JOIN telemetry_packets t ON t.satellite_id = s.satellite_id "
            "GROUP BY s.satellite_id, s.satellite_name, s.orbit_type, s.status "
            "ORDER BY s.satellite_id ASC"
        )
        recent = self.query(
            "SELECT t.packet_id, t.payload_mode, t.signal_strength_dbm, "
            "t.status_msg, t.logged_at, s.satellite_name "
            "FROM telemetry_packets t "
            "JOIN satellites s ON t.satellite_id = s.satellite_id "
            f"ORDER BY t.logged_at DESC LIMIT 8"
        )
        return {
            "total_packets": int(totals.get("total_packets") or 0),
            "total_satellites": int(satellites.get("total") or 0),
            "active_satellites": int(active.get("total") or 0),
            "avg_dbm": float(avg.get("avg_dbm") or 0.0),
            "mode_breakdown": modes,
            "per_satellite": per_satellite,
            "recent": recent,
        }

    def mode_series(self, mode: str, limit: int = 40) -> List[Dict[str, Any]]:
        """Time series of signal strength for one payload mode."""
        ph = self._ph
        return self.query(
            "SELECT t.logged_at, t.signal_strength_dbm, s.satellite_name "
            "FROM telemetry_packets t "
            "JOIN satellites s ON t.satellite_id = s.satellite_id "
            f"WHERE t.payload_mode = {ph} "
            f"ORDER BY t.logged_at DESC LIMIT {int(limit)}",
            (mode,),
        )
