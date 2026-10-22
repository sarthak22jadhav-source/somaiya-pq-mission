# SomaiyaSat &amp; SomaiyaPod — PocketQube Mission Dashboard

A production-ready Flask dashboard for the **SomaiyaSat / SomaiyaPod** PocketQube
mission. It visualises the onboard payload suite, ingests ground-station
telemetry, and demonstrates the AI inter-satellite routing concept.

The backend talks to **Supabase (PostgreSQL)** through `DATABASE_URL` and
`psycopg2`. There is **no automatic SQLite fallback** — if the connection is
missing or fails, the app stops at startup with a clear error instead of
quietly writing to a local file.

---

## Features

| Area | Detail |
| --- | --- |
| **Dashboard** (`/`) | Stat tiles, mission-node list with packet share, payload mix breakdown, recent packets, node registration form |
| **Payloads** (`/payloads`) | Catalogue of M17, Codec2, SSTV and TT&amp;C with live packet counts and average link margin |
| **Telemetry** (`/telemetry`) | Filterable packet log (node / mode / free-text / sort) plus a validated "log new packet" form |
| **JSON API** | `/api/telemetry`, `/api/stats`, `/health` for ground-station or CI integration |
| **Logo** (`/logo`) | Mission mark, element-by-element design breakdown, colour palette and regeneration instructions — chart-free |
| **Logo generator** | Generated with **Tkinter** and captured through the Windows GDI `PrintWindow` path — no Pillow or Ghostscript needed |

---

## Quick start

```bash
cd somaiya-pq-mission

# 1. Install dependencies
pip install -r requirements.txt

# 2. Generate the mission logo (writes static/logo.png)
python logo_generator.py

# 3. Point the app at your Supabase project
copy .env.example .env      # then paste your DATABASE_URL into .env

# 4. Run the dashboard
python app.py
```

Open <http://127.0.0.1:5000>.

The `satellites` and `telemetry_packets` tables are created and seeded
automatically on first start, together with three mission nodes and eight
sample telemetry packets. On success the console logs:

```
Connected to Supabase (PostgreSQL) successfully: postgres@postgres (PostgreSQL 16.2) | tables ready (satellites, telemetry_packets)
```

---

## Connecting to Supabase

1. In your Supabase project go to **Project Settings → Database → Connection
   string** and copy the **URI** format.
2. Copy the template and fill it in:

   ```bash
   cp .env.example .env
   ```

   ```ini
   DATABASE_URL=postgresql://postgres.your-ref:your-password@db.your-ref.supabase.co:5432/postgres?sslmode=require
   ```

3. Restart the app. The header badge reads `SUPABASE / POSTGRES`.

> **Important — percent-encode the password.** Reserved characters such as
> `@` must be percent-encoded (`@` → `%40`), otherwise libpq treats the rest
> of the string as the host name and the connection fails with a confusing
> "could not translate host name" error. `database.py` also normalises the
> URL automatically, so a raw `@` is repaired before the connection is made.

The `.env` file is read by a small dependency-free loader in `database.py`;
real environment variables always take precedence over the file.

> **Note:** the `DATABASE_URL` must point at a project whose network access
> allows your machine (Supabase's direct connection port `5432` is reachable
> from most networks; the pooler on `6543` is the alternative). The first run
> against a live project creates the `satellites` and `telemetry_packets`
> tables automatically.

---

## Project layout

```
somaiya-pq-mission/
├── app.py                  # Flask application factory, routes and validation
├── database.py             # Supabase/PostgreSQL data-access layer
├── logo_generator.py       # Tkinter mission-logo generator (Windows GDI capture)
├── smoke_test.py           # In-process test suite (40 checks)
├── pg_path_test.py         # PostgreSQL/Supabase code-path validation (24 checks)
├── test_supabase_connection.py # Strict Supabase connection tests (17 checks)
├── http_check.py           # Live HTTP checks against a running server
├── requirements.txt
├── .env.example
├── run_live_checks.ps1     # Start server -> run HTTP checks -> stop server
├── run_screenshots.ps1     # Start server -> capture screenshots -> stop server
├── templates/
│   ├── base.html           # Layout, navigation, flash messages
│   ├── macros.html         # Reusable chips and inline SVG icons
│   ├── index.html          # Dashboard
│   ├── payloads.html       # Payload catalogue
│   ├── logo.html           # Mission logo, design breakdown and palette
│   └── telemetry.html      # Packet log + entry form
└── static/
    ├── logo.png            # Generated mission logo
    └── css/app.css         # Custom styles layered on Tailwind
```

---

## Database schema

```sql
CREATE TABLE satellites (
    satellite_id   INT PRIMARY KEY,
    satellite_name TEXT NOT NULL,
    orbit_type     TEXT,
    launch_year    INT,
    status         TEXT
);

CREATE TABLE telemetry_packets (
    packet_id            SERIAL PRIMARY KEY,
    satellite_id         INT REFERENCES satellites(satellite_id),
    payload_mode         TEXT,
    signal_strength_dbm  DECIMAL(6,2),
    status_msg           TEXT,
    logged_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

All statements are parameterised. The `ORDER BY` column is validated against
an allow-list, so it cannot be used for injection.

---

## Tests

```bash
python smoke_test.py
```

Runs 40 checks in-process against a throw-away SQLite database: every route,
the seeded data, both form submissions, all validation failures, every
filter/sort path, the chart-free `/logo` page, and confirmation that an
invalid `order_by` is ignored.

```bash
python pg_path_test.py
```

Runs 24 checks against the PostgreSQL code path using a recording fake
connection. It verifies the `SERIAL` DDL, the `RETURNING packet_id` insert,
that `%s` placeholders are used throughout with no SQLite `?` leakage, that
user input is always bound rather than interpolated, and that the `ORDER BY`
allow-list rejects injection attempts.

```bash
python test_supabase_connection.py
```

Runs 17 checks on the strict Supabase connection logic: that a missing
`DATABASE_URL` raises instead of silently selecting SQLite, that SQLite is
only used when explicitly requested, that a raw `@` in the password is
percent-encoded so the host is parsed correctly, and that the DSN handed to
`psycopg2.connect` is well formed.

```bash
powershell -ExecutionPolicy Bypass -File run_live_checks.ps1
```

Boots the real server, exercises 14 URLs plus a real form POST over HTTP,
then shuts the server down.

---

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | — | **Required.** Supabase PostgreSQL connection URI |
| `DB_CONNECT_TIMEOUT` | `10` | Seconds to wait for the connection handshake |
| `FLASK_SECRET_KEY` | dev placeholder | Session/flash signing key — **change this** |
| `FLASK_HOST` | `127.0.0.1` | Bind address |
| `FLASK_PORT` | `5000` | Bind port |
| `FLASK_DEBUG` | `0` | Set to `1` for the reloading dev server |

---

## Notes & limitations

- **Development server.** `python app.py` uses Flask's built-in server. For
  deployment put it behind a WSGI server such as Gunicorn or waitress.
- **Tailwind via CDN.** Styling loads from `cdn.tailwindcss.com`, so the
  dashboard needs network access on first paint. For an offline deployment,
  install Tailwind and compile a static stylesheet instead.
- **Logo generation is Windows-first.** On Windows the Tk canvas is captured
  with `PrintWindow(PW_RENDERFULLCONTENT)`, which renders the widget's own
  device context and is therefore unaffected by overlapping windows. On other
  platforms the script falls back to `PIL.ImageGrab`, which requires the
  window to be visible and unobstructed, and needs Pillow installed. The
  dashboard regenerates `static/logo.png` on demand when the file is missing;
  if generation fails the `/logo` page still renders and explains how to
  produce the artwork manually.
- **Payload specification values** (band, power, duty cycle) are the nominal
  mission figures used by the catalogue view; replace them with the values
  from your final flight documentation.
