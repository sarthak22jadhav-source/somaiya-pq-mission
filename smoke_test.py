"""
Smoke test for the SomaiyaSat mission dashboard.

Exercises every route and both form submissions against a temporary SQLite
database, so the test never touches the development database.

Run with:  python smoke_test.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import create_app  # noqa: E402
from database import Database  # noqa: E402


def run() -> int:
    failures = []

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "test.db", use_sqlite=True)
        app = create_app(db)
        app.config.update(TESTING=True, SECRET_KEY="test-secret")
        client = app.test_client()

        def check(label, condition, extra=""):
            status = "PASS" if condition else "FAIL"
            print(f"[{status}] {label}" + (f" -- {extra}" if extra else ""))
            if not condition:
                failures.append(label)

        print("=" * 62)
        print("SomaiyaSat / SomaiyaPod mission dashboard - smoke test")
        print("=" * 62)

        # ---------------- GET routes ----------------
        r = client.get("/")
        check("GET / renders", r.status_code == 200, f"status={r.status_code}")
        check("GET / shows mission title", b"SomaiyaSat" in r.data)

        r = client.get("/payloads")
        check("GET /payloads renders", r.status_code == 200, f"status={r.status_code}")
        for mode in (b"M17", b"Codec2", b"SSTV", b"TT&amp;C"):
            check(f"/payloads lists {mode.decode()}", mode in r.data)

        r = client.get("/logo")
        check("GET /logo renders", r.status_code == 200, f"status={r.status_code}")
        check("/logo embeds the logo image", b"logo.png" in r.data)
        check("/logo shows the palette", b"#22D3EE" in r.data)
        check("/logo shows the design breakdown", b"Design breakdown" in r.data)
        check("/logo has no chart library", b"chart" not in r.data.lower())

        r = client.get("/telemetry")
        check("GET /telemetry renders", r.status_code == 200, f"status={r.status_code}")
        check("/telemetry has the form", b"Log new packet" in r.data)

        r = client.get("/api/telemetry")
        check("GET /api/telemetry returns JSON", r.status_code == 200 and r.is_json)
        check("/api/telemetry has packets", r.get_json()["count"] > 0)

        r = client.get("/api/stats")
        check("GET /api/stats returns JSON", r.status_code == 200 and r.is_json)
        check("/api/stats has totals", r.get_json()["total_packets"] > 0)

        r = client.get("/health")
        check("GET /health returns JSON", r.status_code == 200 and r.is_json)

        r = client.get("/static/logo.png")
        check("GET /static/logo.png serves", r.status_code == 200, f"status={r.status_code}")

        r = client.get("/static/css/app.css")
        check("GET /static/css/app.css serves", r.status_code == 200)

        # ---------------- Seeded data ----------------
        sats = db.list_satellites()
        check("3 default satellites seeded", len(sats) == 3, f"got {len(sats)}")
        names = [s["satellite_name"] for s in sats]
        check(
            "SomaiyaSat / SomaiyaPod / ReLAY seeded",
            names == ["SomaiyaSat", "SomaiyaPod", "SomaiyaPod-ReLAY"],
            str(names),
        )

        # ---------------- POST telemetry (valid) ----------------
        before = db.telemetry_stats()["total_packets"]
        r = client.post(
            "/telemetry",
            data={
                "satellite_id": "1",
                "payload_mode": "Codec2",
                "signal_strength_dbm": "-77.25",
                "status_msg": "Smoke test voice burst decoded",
            },
            follow_redirects=True,
        )
        check("POST /telemetry redirects", r.status_code == 200, f"status={r.status_code}")
        after = db.telemetry_stats()["total_packets"]
        check("POST /telemetry stored a packet", after == before + 1, f"{before} -> {after}")
        check("POST /telemetry shows success flash", b"logged" in r.data)

        # ---------------- POST telemetry (invalid) ----------------
        before = db.telemetry_stats()["total_packets"]
        r = client.post(
            "/telemetry",
            data={
                "satellite_id": "1",
                "payload_mode": "NotAMode",
                "signal_strength_dbm": "9999",
                "status_msg": "",
            },
            follow_redirects=True,
        )
        after = db.telemetry_stats()["total_packets"]
        check("invalid payload mode rejected", b"Payload mode" in r.data)
        check("out-of-range dBm rejected", b"between -140 and 0" in r.data)
        check("empty message rejected", b"Status message" in r.data)
        check("invalid POST stored nothing", after == before, f"{before} -> {after}")

        # ---------------- POST satellite ----------------
        before = len(db.list_satellites())
        r = client.post(
            "/satellites",
            data={
                "satellite_name": "SomaiyaPod-2",
                "orbit_type": "SSO",
                "launch_year": "2027",
                "status": "Standby",
            },
            follow_redirects=True,
        )
        check("POST /satellites redirects", r.status_code == 200, f"status={r.status_code}")
        after = len(db.list_satellites())
        check("POST /satellites added a node", after == before + 1, f"{before} -> {after}")
        check(
            "new node persisted",
            any(s["satellite_name"] == "SomaiyaPod-2" for s in db.list_satellites()),
        )

        r = client.post(
            "/satellites", data={"satellite_name": ""}, follow_redirects=True
        )
        check("empty satellite name rejected", b"name is required" in r.data)

        # ---------------- Filtering ----------------
        r = client.get("/telemetry?payload_mode=M17")
        check("filter by mode works", r.status_code == 200 and b"M17" in r.data)

        r = client.get("/telemetry?satellite_id=2")
        check("filter by satellite works", r.status_code == 200)

        r = client.get("/telemetry?q=beacon")
        check("free-text search works", r.status_code == 200)

        r = client.get("/telemetry?order_by=signal_strength_dbm&order_dir=asc")
        check("custom ordering works", r.status_code == 200)

        r = client.get("/telemetry?order_by=DROP+TABLE")
        check("invalid order_by is ignored safely", r.status_code == 200)

        # ---------------- Re-seeding is idempotent ----------------
        db.seed_defaults()
        check("seeding is idempotent", len(db.list_satellites()) == after)

        # Release the SQLite handle so Windows can delete the temp folder.
        db.close()

        print("=" * 62)
        if failures:
            print(f"{len(failures)} CHECK(S) FAILED:")
            for f in failures:
                print(f"  - {f}")
            return 1
        print("ALL CHECKS PASSED")
        return 0


if __name__ == "__main__":
    raise SystemExit(run())
