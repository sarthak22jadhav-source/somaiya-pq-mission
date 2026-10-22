"""
SomaiyaSat & SomaiyaPod - PocketQube Mission Dashboard
Flask application factory and routes.

Routes
------
GET  /                    Mission overview dashboard
GET  /payloads            Payload mode catalogue (M17, Codec2, SSTV, TT&C)
GET  /logo                Mission logo artwork, design notes and colour palette
GET  /telemetry           Telemetry log with filtering + new-packet form
POST /telemetry           Persist a new telemetry packet
POST /satellites          Register a new satellite node
GET  /api/telemetry        JSON feed of telemetry rows
GET  /api/stats            JSON feed of aggregate statistics
GET  /health               Lightweight liveness probe
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from database import (
    BASE_DIR,
    ORBIT_TYPES,
    PAYLOAD_MODES,
    SATELLITE_STATUSES,
    Database,
    load_dotenv,
)
from logo_generator import DESIGN_ELEMENTS, PALETTE, ensure_logo

# Load a local .env (if present) before the database layer reads the env.
load_dotenv(BASE_DIR / ".env")

# Path of the mission logo artwork served from the static folder.
LOGO_PATH = Path(BASE_DIR) / "static" / "logo.png"

# Payload catalogue used by the /payloads view.
PAYLOAD_CATALOGUE: List[Dict[str, Any]] = [
    {
        "mode": "M17",
        "name": "M17 Digital Beacon",
        "icon": "radio-tower",
        "accent": "cyan",
        "summary": "Low-duty-cycle digital beacon that identifies the satellite to ground stations.",
        "details": (
            "M17 is the primary uplink beacon. It is transmitted continuously at low "
            "power and is used for station acquisition, Doppler referencing and coarse "
            "orbital determination. The payload is designed to survive the limited "
            "thermal and power budget of a PocketQube-class CubeSat."
        ),
        "band": "145.8 MHz",
        "power": "0.5 W",
        "duty_cycle": "100%",
        "tags": ["Beacon", "Doppler", "Acquisition"],
    },
    {
        "mode": "Codec2",
        "name": "Codec2 Voice Link",
        "icon": "mic",
        "accent": "magenta",
        "summary": "Ultra-low bit-rate digital voice downlink for two-way amateur radio contact.",
        "details": (
            "Codec2 delivers intelligible voice at 700 bits/s, small enough to run on "
            "a PocketQube downlink. The mission uses it to demonstrate AI-assisted "
            "routing between SomaiyaSat and SomaiyaPod when a ground station is only "
            "reachable by one of the two nodes."
        ),
        "band": "145.8 MHz",
        "power": "2.0 W",
        "duty_cycle": "On demand",
        "tags": ["Voice", "Two-way", "AI routed"],
    },
    {
        "mode": "SSTV",
        "name": "SSTV Image Beacon",
        "icon": "image",
        "accent": "amber",
        "summary": "Slow-scan television frames carrying mission imagery and telemetry overlays.",
        "details": (
            "SSTV is the highest-bandwidth mode and is therefore scheduled during "
            "station passes. Frames carry a mission status overlay generated onboard, "
            "which gives ground operators a quick visual confirmation that the payload "
            "chain - sensor, encoder, transmitter - is healthy."
        ),
        "band": "145.8 MHz",
        "power": "3.0 W",
        "duty_cycle": "Scheduled",
        "tags": ["Imagery", "Overlay", "Scheduled"],
    },
    {
        "mode": "TT&C",
        "name": "TT&C Housekeeping",
        "icon": "activity",
        "accent": "emerald",
        "summary": "Housekeeping telemetry: bus voltage, temperature, attitude and link margin.",
        "details": (
            "Telemetry, tracking and command is the always-on channel that reports the "
            "state of the satellite bus. It underpins the dashboard, because every "
            "signal-strength sample shown here originates from a TT&C housekeeping "
            "frame captured by the ground station."
        ),
        "band": "145.8 MHz",
        "power": "1.0 W",
        "duty_cycle": "100%",
        "tags": ["Housekeeping", "Health", "Always on"],
    },
]

def create_app(database: Optional[Database] = None) -> Flask:
    """Build and configure the Flask application.

    The database backend is Supabase/PostgreSQL only. The application fails
    fast at startup when the Supabase connection cannot be established rather
    than silently degrading to a local SQLite file.
    """
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv(
        "FLASK_SECRET_KEY", "somaiya-pocketqube-dev-secret-change-me"
    )
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    db: Database = database or Database()
    app.config["DB"] = db

    # Connect to Supabase, create the tables and seed the default rows.
    try:
        info = db.connect()
        db.init_db()
    except Exception as exc:  # pragma: no cover - startup diagnostics
        target = "Supabase (PostgreSQL)" if db.backend == "postgresql" else "SQLite"
        app.logger.error(
            "Failed to connect to %s: %s: %s",
            target,
            type(exc).__name__,
            exc,
        )
        db.last_error = f"{type(exc).__name__}: {exc}"
        raise

    if db.backend == "postgresql":
        app.logger.info(
            "Connected to Supabase (PostgreSQL) successfully: %s | "
            "tables ready (satellites, telemetry_packets)",
            info,
        )
    else:
        app.logger.info(
            "Connected to local SQLite database successfully: %s | "
            "tables ready (satellites, telemetry_packets)",
            info,
        )

    # ------------------------------------------------------------------ #
    # Template helpers
    # ------------------------------------------------------------------ #
    @app.context_processor
    def inject_globals() -> Dict[str, Any]:
        return {
            "payload_modes": PAYLOAD_MODES,
            "orbit_types": ORBIT_TYPES,
            "satellite_statuses": SATELLITE_STATUSES,
            "db_backend": db.backend,
            "db_error": db.last_error,
            "current_year": datetime.now().year,
        }

    # ------------------------------------------------------------------ #
    # Routes
    # ------------------------------------------------------------------ #
    @app.route("/")
    def index() -> str:
        stats = _safe(db.telemetry_stats, {})
        satellites = _safe(db.list_satellites, [])
        catalogue = PAYLOAD_CATALOGUE
        return render_template(
            "index.html",
            stats=stats,
            satellites=satellites,
            catalogue=catalogue,
            page="dashboard",
        )

    @app.route("/payloads")
    def payloads() -> str:
        stats = _safe(db.telemetry_stats, {})
        satellites = _safe(db.list_satellites, [])
        return render_template(
            "payloads.html",
            catalogue=PAYLOAD_CATALOGUE,
            stats=stats,
            satellites=satellites,
            page="payloads",
        )

    @app.route("/logo")
    def logo() -> str:
        # Make sure the artwork exists; generation is best-effort so a missing
        # Tkinter/display never blocks the dashboard from booting.
        logo_available = LOGO_PATH.exists() and LOGO_PATH.stat().st_size > 0
        if not logo_available:
            try:
                ensure_logo(LOGO_PATH)
                logo_available = True
            except Exception as exc:  # pragma: no cover - artwork is optional
                app.logger.warning("Could not generate the mission logo: %s", exc)

        return render_template(
            "logo.html",
            palette=PALETTE,
            design_elements=DESIGN_ELEMENTS,
            logo_available=logo_available,
            logo_size=LOGO_PATH.stat().st_size if logo_available else 0,
            page="logo",
        )

    @app.route("/telemetry", methods=["GET", "POST"])
    def telemetry() -> Any:
        if request.method == "POST":
            return _handle_telemetry_post(db)

        satellites = _safe(db.list_satellites, [])
        selected_satellite = request.args.get("satellite_id", type=int)
        selected_mode = request.args.get("payload_mode") or None
        search = request.args.get("q") or None
        order_by = request.args.get("order_by", "logged_at")
        order_dir = request.args.get("order_dir", "desc")

        rows = _safe(
            lambda: db.list_telemetry(
                satellite_id=selected_satellite,
                payload_mode=selected_mode,
                status_like=search,
                order_by=order_by,
                order_dir=order_dir,
                limit=200,
            ),
            [],
        )
        stats = _safe(db.telemetry_stats, {})
        return render_template(
            "telemetry.html",
            rows=rows,
            satellites=satellites,
            stats=stats,
            selected_satellite=selected_satellite,
            selected_mode=selected_mode,
            search=search or "",
            order_by=order_by,
            order_dir=order_dir,
            page="telemetry",
        )

    @app.route("/satellites", methods=["POST"])
    def add_satellite() -> Any:
        name = (request.form.get("satellite_name") or "").strip()
        orbit = (request.form.get("orbit_type") or "LEO").strip()
        status = (request.form.get("status") or "Active").strip()
        year_raw = (request.form.get("launch_year") or "").strip()

        if not name:
            flash("Satellite name is required.", "error")
            return redirect(url_for("index"))

        launch_year: Optional[int] = None
        if year_raw:
            try:
                launch_year = int(year_raw)
            except ValueError:
                flash("Launch year must be a number.", "error")
                return redirect(url_for("index"))

        try:
            db.add_satellite(
                satellite_name=name,
                orbit_type=orbit if orbit in ORBIT_TYPES else "LEO",
                launch_year=launch_year,
                status=status if status in SATELLITE_STATUSES else "Active",
            )
            flash(f"Satellite node '{name}' registered.", "success")
        except Exception as exc:
            app.logger.error("Failed to add satellite: %s", exc)
            flash(f"Could not register satellite: {exc}", "error")

        return redirect(url_for("index"))

    @app.route("/api/telemetry")
    def api_telemetry() -> Any:
        rows = _safe(
            lambda: db.list_telemetry(
                satellite_id=request.args.get("satellite_id", type=int),
                payload_mode=request.args.get("payload_mode") or None,
                status_like=request.args.get("q") or None,
                limit=request.args.get("limit", 100, type=int),
            ),
            [],
        )
        return jsonify({"count": len(rows), "packets": rows})

    @app.route("/api/stats")
    def api_stats() -> Any:
        return jsonify(_safe(db.telemetry_stats, {}))

    @app.route("/health")
    def health() -> Any:
        return jsonify(
            {
                "status": "ok",
                "backend": db.backend,
                "database_error": db.last_error,
                "timestamp": datetime.now().isoformat(),
            }
        )

    return app


def _safe(func, default):
    """Run a database call, returning ``default`` instead of raising."""
    try:
        return func()
    except Exception:
        return default


def _handle_telemetry_post(db: Database) -> Any:
    """Validate and persist a new telemetry packet submitted from the form."""
    satellite_raw = (request.form.get("satellite_id") or "").strip()
    mode = (request.form.get("payload_mode") or "").strip()
    strength_raw = (request.form.get("signal_strength_dbm") or "").strip()
    message = (request.form.get("status_msg") or "").strip()

    errors: List[str] = []

    satellite_id: Optional[int] = None
    if satellite_raw:
        try:
            satellite_id = int(satellite_raw)
        except ValueError:
            errors.append("Satellite selection is invalid.")

    if mode not in PAYLOAD_MODES:
        errors.append("Payload mode must be one of: " + ", ".join(PAYLOAD_MODES))

    signal_strength: Optional[float] = None
    if strength_raw:
        try:
            signal_strength = float(strength_raw)
        except ValueError:
            errors.append("Signal strength must be a number.")
        else:
            if not -140.0 <= signal_strength <= 0.0:
                errors.append("Signal strength must be between -140 and 0 dBm.")

    if not message:
        errors.append("Status message is required.")

    if errors:
        for err in errors:
            flash(err, "error")
        return redirect(url_for("telemetry"))

    try:
        packet_id = db.add_telemetry_packet(
            satellite_id=satellite_id if satellite_id is not None else 1,
            payload_mode=mode,
            signal_strength_dbm=signal_strength,
            status_msg=message,
        )
        flash(f"Telemetry packet #{packet_id} logged.", "success")
    except Exception as exc:
        flash(f"Could not log telemetry packet: {exc}", "error")

    return redirect(url_for("telemetry"))


_app: Optional[Flask] = None


def get_app() -> Flask:
    """Return the module-level Flask application, creating it on first use.

    The application is built lazily so that importing this module (for the
    test-suite, or for ``flask --app app:create_app``) does not require a
    live Supabase connection. The moment the app is actually used, the
    Supabase connection is established and any failure is raised.
    """
    global _app
    if _app is None:
        _app = create_app()
    return _app


def __getattr__(name: str):
    """Expose ``app`` lazily for ``flask --app app run`` (PEP 562)."""
    if name == "app":
        return get_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if __name__ == "__main__":
    host = os.getenv("FLASK_HOST", "127.0.0.1")
    port = int(os.getenv("FLASK_PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "0") not in ("0", "false", "False", "")
    get_app().run(host=host, port=port, debug=debug)
