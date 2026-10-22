"""
Live HTTP checks against a running dashboard instance.

Usage:  python http_check.py [base_url]
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000").rstrip("/")

PATHS = [
    "/",
    "/payloads",
    "/telemetry",
    "/static/logo.png",
    "/static/css/app.css",
    "/health",
    "/api/stats",
    "/api/telemetry",
    "/telemetry?payload_mode=M17",
    "/telemetry?payload_mode=Codec2",
    "/telemetry?satellite_id=2",
    "/telemetry?q=beacon",
    "/telemetry?order_by=signal_strength_dbm&order_dir=asc",
    "/telemetry?order_by=DROP+TABLE",
]


def main() -> int:
    failures = []
    print("=" * 68)
    print(f"Live HTTP checks against {BASE}")
    print("=" * 68)

    for path in PATHS:
        url = BASE + path
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                body = resp.read()
                print(f"[PASS] {resp.status}  {path:<45} {len(body):>8} bytes")
                if resp.status != 200:
                    failures.append(path)
        except urllib.error.HTTPError as exc:
            print(f"[FAIL] {exc.code}  {path:<45} {exc.reason}")
            failures.append(path)
        except Exception as exc:
            print(f"[FAIL] ERR  {path:<45} {exc}")
            failures.append(path)

    # ---- JSON payload sanity ----
    for path in ("/api/stats", "/api/telemetry", "/health"):
        try:
            with urllib.request.urlopen(BASE + path, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            print(f"[PASS] JSON valid  {path}")
            if path == "/api/telemetry" and data.get("count", 0) < 1:
                print("[FAIL] /api/telemetry returned zero packets")
                failures.append(path)
            if path == "/health" and data.get("status") != "ok":
                print("[FAIL] /health status is not ok")
                failures.append(path)
        except Exception as exc:
            print(f"[FAIL] JSON invalid {path}: {exc}")
            failures.append(path)

    # ---- form submission over real HTTP ----
    form = urllib.parse.urlencode(
        {
            "satellite_id": "1",
            "payload_mode": "M17",
            "signal_strength_dbm": "-72.50",
            "status_msg": "Live HTTP check packet",
        }
    ).encode()

    try:
        req = urllib.request.Request(
            BASE + "/telemetry", data=form, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", "ignore")
        ok = resp.status == 200 and "Live HTTP check packet" in body
        print(f"[{'PASS' if ok else 'FAIL'}] POST /telemetry stored a packet")
        if not ok:
            failures.append("POST /telemetry")
    except Exception as exc:
        print(f"[FAIL] POST /telemetry raised {exc}")
        failures.append("POST /telemetry")

    print("=" * 68)
    if failures:
        print(f"{len(failures)} FAILURE(S): {failures}")
        return 1
    print("ALL LIVE HTTP CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
