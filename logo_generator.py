"""
SomaiyaSat & SomaiyaPod - PocketQube Mission
Mission logo generator (Tkinter Canvas).

Draws a professional space / satellite themed mission logo on a Tkinter
``Canvas`` and automatically saves it as a PNG that the web dashboard embeds
in its navigation bar.

Capture strategy
----------------
Tk's own ``postscript()`` output needs Ghostscript to rasterise, which is not
available everywhere. Instead the finished canvas is captured straight from the
Windows GDI using ``PrintWindow(PW_RENDERFULLCONTENT)`` via ``ctypes``. This
renders the widget's own device context, so the result is pixel-accurate even
when the window is partially covered by another window (a plain screen grab
would capture whatever is on top of it).

On non-Windows platforms the script falls back to a Pillow-based re-render of
the same design so the logo can still be produced during development.

Usage
-----
    python logo_generator.py                 # writes static/logo.png
    python logo_generator.py --out static/logo.png --size 900x420
"""

from __future__ import annotations

import argparse
import ctypes
import math
import os
import struct
import sys
import zlib
from ctypes import wintypes
from pathlib import Path
from typing import Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = BASE_DIR / "static" / "logo.png"

# --------------------------------------------------------------------------- #
# Mission palette (aerospace dark theme)
# --------------------------------------------------------------------------- #
SPACE_BG = "#050B18"
DEEP_BLUE = "#0B1B3A"
PANEL_BLUE = "#122B54"
NEON_CYAN = "#22D3EE"
CYAN_DIM = "#0E7490"
MAGENTA = "#E879F9"
GOLD = "#FBBF24"
STAR_WHITE = "#E2E8F0"
MUTED = "#64748B"

WIDTH = 900
HEIGHT = 420


# --------------------------------------------------------------------------- #
# Logo metadata shared with the web dashboard
# --------------------------------------------------------------------------- #
#: Human-readable name of each colour in the mission palette. The web
#: ``/logo`` page renders this mapping so the documentation and the generated
#: PNG can never drift apart.
PALETTE: List[Dict[str, str]] = [
    {"name": "Deep Space", "hex": SPACE_BG, "role": "Primary backdrop"},
    {"name": "Orbit Blue", "hex": DEEP_BLUE, "role": "Planet core and starfield"},
    {"name": "Panel Blue", "hex": PANEL_BLUE, "role": "Satellite bus and panel faces"},
    {"name": "Neon Cyan", "hex": NEON_CYAN, "role": "Primary accent and wordmark"},
    {"name": "Cyan Dim", "hex": CYAN_DIM, "role": "Orbit lines and panel grids"},
    {"name": "Signal Magenta", "hex": MAGENTA, "role": "AI inter-satellite link"},
    {"name": "Mission Gold", "hex": GOLD, "role": "Payload and capability tags"},
    {"name": "Star White", "hex": STAR_WHITE, "role": "Primary typography"},
    {"name": "Slate Muted", "hex": MUTED, "role": "Secondary typography"},
]

#: Description of every graphic element drawn on the canvas.
DESIGN_ELEMENTS: List[Dict[str, str]] = [
    {
        "name": "Emblem",
        "detail": (
            "A stylised Earth core sits inside a soft radial nebula glow, "
            "orbited by two dashed orbital tracks."
        ),
    },
    {
        "name": "SomaiyaSat",
        "detail": (
            "The satellite rides the inner orbit at the upper right, drawn with "
            "a panelled bus, two solar wings and a directional dish."
        ),
    },
    {
        "name": "SomaiyaPod",
        "detail": (
            "The ground node rides the outer orbit at the lower left as a "
            "parabolic antenna dish with a live feed horn."
        ),
    },
    {
        "name": "AI routing link",
        "detail": (
            "A dashed magenta link connects both nodes, with concentric radio "
            "waves showing the downlink to the ground."
        ),
    },
    {
        "name": "Wordmark",
        "detail": (
            "SOMAIYA set in heavy caps above the cyan SOMAIYAPOD lockup, "
            "underlined by a cyan rule and the PocketQube mission subtitle."
        ),
    },
    {
        "name": "Capability strip",
        "detail": (
            "A gold line lists the on-board suite - AI routing, M17, Codec2, "
            "SSTV and TT&C - above three PQ / AI / RF mission badges."
        ),
    },
]


# --------------------------------------------------------------------------- #
# Minimal PNG writer (stdlib only, no Pillow required)
# --------------------------------------------------------------------------- #
def _png_chunk(tag: bytes, data: bytes) -> bytes:
    payload = tag + data
    return (
        struct.pack(">I", len(data))
        + payload
        + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)
    )


def write_png(path: Path, width: int, height: int, rows: List[bytes]) -> None:
    """Write 8-bit RGB scanlines (each prefixed with a filter byte) as a PNG."""
    raw = b"".join(rows)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    blob = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(raw, 9))
        + _png_chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)


# --------------------------------------------------------------------------- #
# Windows GDI capture of a Tk widget
# --------------------------------------------------------------------------- #
def _capture_widget_hwnd(hwnd: int, width: int, height: int) -> Optional[List[bytes]]:
    """Return PNG-ready RGB scanlines captured from a Tk widget HWND."""
    if sys.platform != "win32":
        return None

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

    hdc = user32.GetDC(hwnd)
    if not hdc:
        return None
    mem_dc = gdi32.CreateCompatibleDC(hdc)
    bitmap = gdi32.CreateCompatibleBitmap(hdc, width, height)
    previous = gdi32.SelectObject(mem_dc, bitmap)

    # PW_RENDERFULLCONTENT (0x2) makes the widget render itself into the DC.
    rendered = user32.PrintWindow(hwnd, mem_dc, 2)

    info = BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.bmiHeader.biWidth = width
    info.bmiHeader.biHeight = -height  # negative height => top-down rows
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = 0  # BI_RGB

    buffer = ctypes.create_string_buffer(width * height * 4)
    copied = gdi32.GetDIBits(mem_dc, bitmap, 0, height, buffer, ctypes.byref(info), 0)

    gdi32.SelectObject(mem_dc, previous)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(hwnd, hdc)

    if not rendered or not copied:
        return None

    raw = buffer.raw
    rows: List[bytes] = []
    for y in range(height):
        row = bytearray(b"\x00")
        base = y * width * 4
        for x in range(width):
            blue, green, red = raw[base + x * 4: base + x * 4 + 3]
            row += bytes((red, green, blue))
        rows.append(bytes(row))
    return rows


# --------------------------------------------------------------------------- #
# Drawing helpers
# --------------------------------------------------------------------------- #
def _hex_to_rgb(colour: str) -> Tuple[int, int, int]:
    colour = colour.lstrip("#")
    return (int(colour[0:2], 16), int(colour[2:4], 16), int(colour[4:6], 16))


def _blend(a: str, b: str, t: float) -> str:
    """Blend two hex colours; ``t`` = 0 gives ``a``, ``t`` = 1 gives ``b``."""
    ar, ag, ab = _hex_to_rgb(a)
    br, bg, bb = _hex_to_rgb(b)
    return "#%02X%02X%02X" % (
        int(ar + (br - ar) * t),
        int(ag + (bg - ag) * t),
        int(ab + (bb - ab) * t),
    )


def _fit_font_size(canvas, text: str, max_size: int, max_width: float,
                   family: str = "Arial", weight: str = "bold") -> int:
    """Return the largest font size at which ``text`` fits inside ``max_width``."""
    try:
        import tkinter.font as tkfont

        font = tkfont.Font(root=canvas, family=family, size=max_size, weight=weight)
        measured = font.measure(text)
        if measured <= max_width:
            return max_size
        scaled = int(max_size * (max_width / float(measured)))
        return max(10, scaled)
    except Exception:
        return max_size


def _draw_starfield(canvas, width: int, height: int, count: int = 150, seed: int = 20240917) -> None:
    """Deterministic starfield so every build produces an identical logo."""
    state = seed
    for _ in range(count):
        state = (1103515245 * state + 12345) % 2147483648
        x = (state >> 7) % width
        state = (1103515245 * state + 12345) % 2147483648
        y = (state >> 9) % height
        state = (1103515245 * state + 12345) % 2147483648
        size = 1 + (state >> 11) % 2
        brightness = 0.35 + ((state >> 13) % 65) / 100.0
        colour = _blend(DEEP_BLUE, STAR_WHITE, brightness)
        canvas.create_oval(x - size, y - size, x + size, y + size, fill=colour, outline="")


def _draw_orbit_ring(canvas, cx: float, cy: float, radius: float, colour: str, width: int = 3) -> None:
    """Dashed orbital path around the planet/Earth core."""
    steps = 96
    for i in range(0, steps, 2):
        a1 = 2 * math.pi * i / steps
        a2 = 2 * math.pi * (i + 1) / steps
        canvas.create_line(
            cx + radius * math.cos(a1),
            cy + radius * math.sin(a1),
            cx + radius * math.cos(a2),
            cy + radius * math.sin(a2),
            fill=colour,
            width=width,
        )


def _draw_satellite(canvas, x: float, y: float, scale: float, body: str, panel: str) -> None:
    """Draw a stylised satellite: body, solar panels and a dish antenna."""
    bw, bh = 26 * scale, 18 * scale
    canvas.create_rectangle(
        x - bw / 2, y - bh / 2, x + bw / 2, y + bh / 2,
        fill=body, outline=STAR_WHITE, width=max(1, int(scale)),
    )
    # Solar panels
    pw, ph = 20 * scale, 13 * scale
    gap = 6 * scale
    for side in (-1, 1):
        px = x + side * (bw / 2 + gap)
        canvas.create_rectangle(
            px - (pw / 2 if side < 0 else 0),
            y - ph / 2,
            px + (0 if side < 0 else pw / 2),
            y + ph / 2,
            fill=panel,
            outline=NEON_CYAN,
            width=max(1, int(scale * 0.8)),
        )
        for k in range(1, 3):
            yy = y - ph / 2 + (ph / 3) * k
            canvas.create_line(
                px - (pw / 2 if side < 0 else 0), yy,
                px + (0 if side < 0 else pw / 2), yy,
                fill=CYAN_DIM, width=max(1, int(scale * 0.6)),
            )
    # Antenna dish
    canvas.create_arc(
        x - 9 * scale, y - 9 * scale - 16 * scale,
        x + 9 * scale, y + 9 * scale - 16 * scale,
        start=200, extent=140, style="arc", outline=STAR_WHITE, width=max(1, int(scale)),
    )
    canvas.create_line(
        x, y - bh / 2, x, y - 16 * scale, fill=STAR_WHITE, width=max(1, int(scale))
    )


def _draw_antenna_dish(canvas, x: float, y: float, scale: float) -> None:
    """Ground-station style parabolic dish used for the SomaiyaPod node."""
    r = 22 * scale
    canvas.create_arc(
        x - r, y - r, x + r, y + r,
        start=200, extent=140, style="arc", outline=NEON_CYAN, width=max(2, int(2 * scale)),
    )
    canvas.create_line(x, y, x, y - r * 0.9, fill=MUTED, width=max(1, int(scale)))
    canvas.create_oval(
        x - 3 * scale, y - r * 0.9 - 3 * scale,
        x + 3 * scale, y - r * 0.9 + 3 * scale,
        fill=MAGENTA, outline="",
    )
    canvas.create_oval(
        x - r * 0.9, y - 3 * scale, x + r * 0.9, y + 3 * scale,
        fill="", outline=CYAN_DIM, width=max(1, int(scale)),
    )


def _draw_earth_core(canvas, cx: float, cy: float, radius: float) -> None:
    """Stylised Earth core with a terminator gradient and latitude lines."""
    canvas.create_oval(
        cx - radius, cy - radius, cx + radius, cy + radius,
        fill=DEEP_BLUE, outline=NEON_CYAN, width=3,
    )
    # Latitude lines
    for frac in (-0.55, -0.25, 0.0, 0.25, 0.55):
        ry = radius * (1 - abs(frac) * 0.75)
        yy = cy + radius * frac
        canvas.create_oval(
            cx - ry, yy - radius * 0.16, cx + ry, yy + radius * 0.16,
            outline=CYAN_DIM, width=1,
        )
    # Longitude arcs
    for frac in (-0.55, 0.0, 0.55):
        rx = radius * (1 - abs(frac) * 0.6)
        canvas.create_oval(
            cx - rx, cy - radius, cx + rx, cy + radius,
            outline=CYAN_DIM, width=1,
        )
    # Highlight
    canvas.create_oval(
        cx - radius * 0.55, cy - radius * 0.7,
        cx - radius * 0.05, cy - radius * 0.2,
        fill=PANEL_BLUE, outline="",
    )


def _draw_signal_waves(canvas, x: float, y: float, scale: float) -> None:
    """Concentric radio waves emitted by the active node."""
    for i, r in enumerate((10, 17, 24)):
        canvas.create_arc(
            x - r * scale, y - r * scale, x + r * scale, y + r * scale,
            start=210, extent=120, style="arc",
            outline=_blend(NEON_CYAN, DEEP_BLUE, i * 0.3),
            width=max(1, int(1.6 * scale)),
        )


def _draw_badge(canvas, cx: float, cy: float, radius: float, label: str, accent: str) -> None:
    """Circular mission badge with a coloured accent ring and a short label."""
    canvas.create_oval(
        cx - radius, cy - radius, cx + radius, cy + radius,
        fill=SPACE_BG, outline=accent, width=3,
    )
    canvas.create_oval(
        cx - radius + 7, cy - radius + 7, cx + radius - 7, cy + radius - 7,
        outline=_blend(accent, SPACE_BG, 0.55), width=1,
    )
    canvas.create_text(
        cx, cy, text=label, fill=STAR_WHITE,
        font=("Arial", max(8, int(radius * 0.42)), "bold"),
    )


def draw_logo(canvas, width: int, height: int) -> None:
    """Compose the complete SomaiyaSat / SomaiyaPod mission logo."""
    # -- backdrop ---------------------------------------------------------
    canvas.create_rectangle(0, 0, width, height, fill=SPACE_BG, outline="")
    _draw_starfield(canvas, width, height)

    # Soft radial "nebula" glow behind the emblem.
    # Drawn largest-and-dimmest first so the edge feathers into the backdrop.
    glow_cx, glow_cy = width * 0.235, height * 0.5
    glow_max, glow_min = 178.0, 30.0
    for i in range(18):
        t = i / 17.0
        r = glow_max - (glow_max - glow_min) * t
        canvas.create_oval(
            glow_cx - r, glow_cy - r, glow_cx + r, glow_cy + r,
            fill=_blend(SPACE_BG, DEEP_BLUE, 0.10 + 0.55 * t),
            outline="",
        )

    # -- emblem: Earth + two orbits + the two nodes ------------------------
    core_r = 62
    _draw_earth_core(canvas, glow_cx, glow_cy, core_r)

    _draw_orbit_ring(canvas, glow_cx, glow_cy, 104, CYAN_DIM, width=2)
    _draw_orbit_ring(canvas, glow_cx, glow_cy, 138, _blend(CYAN_DIM, SPACE_BG, 0.35), width=2)

    # SomaiyaSat on the inner orbit (upper right)
    sat_x = glow_cx + 104 * math.cos(math.radians(-38))
    sat_y = glow_cy + 104 * math.sin(math.radians(-38))
    _draw_satellite(canvas, sat_x, sat_y, 1.15, PANEL_BLUE, "#0E3A5C")

    # SomaiyaPod on the outer orbit (lower left)
    pod_x = glow_cx + 138 * math.cos(math.radians(148))
    pod_y = glow_cy + 138 * math.sin(math.radians(148))
    _draw_antenna_dish(canvas, pod_x, pod_y, 1.15)

    # AI routing link between the two nodes (dashed magenta path)
    canvas.create_line(
        sat_x, sat_y, pod_x, pod_y, fill=_blend(MAGENTA, SPACE_BG, 0.45),
        width=2, dash=(6, 6),
    )
    _draw_signal_waves(canvas, sat_x + 26, sat_y - 22, 1.0)

    # -- typography --------------------------------------------------------
    text_x = width * 0.44
    text_avail = width - text_x - 28

    title_size = _fit_font_size(canvas, "SOMAIYA", 58, text_avail)
    canvas.create_text(
        text_x, height * 0.29, anchor="w", text="SOMAIYA",
        fill=STAR_WHITE, font=("Arial", title_size, "bold"),
    )

    sub_size = _fit_font_size(canvas, "SAT  &  SOMAIYAPOD", 34, text_avail)
    canvas.create_text(
        text_x, height * 0.44, anchor="w", text="SAT  &  SOMAIYAPOD",
        fill=NEON_CYAN, font=("Arial", sub_size, "bold"),
    )
    canvas.create_line(
        text_x, height * 0.53, width - 28, height * 0.53,
        fill=_blend(NEON_CYAN, SPACE_BG, 0.45), width=2,
    )
    canvas.create_text(
        text_x, height * 0.62, anchor="w", text="POCKETQUBE MISSION",
        fill=MUTED, font=("Arial", 19),
    )
    tag_size = _fit_font_size(
        canvas, "AI ROUTING  //  M17  CODEC2  SSTV  TT&C", 14, text_avail
    )
    canvas.create_text(
        text_x, height * 0.74, anchor="w",
        text="AI ROUTING  //  M17  CODEC2  SSTV  TT&C",
        fill=GOLD, font=("Arial", tag_size, "bold"),
    )

    # Mission badges along the bottom right
    badge_y = height * 0.855
    _draw_badge(canvas, width * 0.44 + 40, badge_y, 26, "PQ", NEON_CYAN)
    _draw_badge(canvas, width * 0.44 + 104, badge_y, 26, "AI", MAGENTA)
    _draw_badge(canvas, width * 0.44 + 168, badge_y, 26, "RF", GOLD)

    canvas.create_text(
        width * 0.44 + 205, badge_y, anchor="w", text="FLIGHT READY",
        fill=_blend(NEON_CYAN, SPACE_BG, 0.2), font=("Arial", 14, "bold"),
    )


def _capture_via_pillow(root, canvas, width: int, height: int) -> Optional[List[bytes]]:
    """Fallback capture path (non-Windows) that requires Pillow."""
    try:
        from PIL import ImageGrab
    except Exception:
        return None

    root.attributes("-topmost", True)
    root.update_idletasks()
    root.update()
    root.lift()
    root.update()

    x = root.winfo_rootx()
    y = root.winfo_rooty()
    image = ImageGrab.grab(bbox=(x, y, x + width, y + height)).convert("RGB")
    data = image.load()
    rows: List[bytes] = []
    for row_y in range(height):
        row = bytearray(b"\x00")
        for col_x in range(width):
            r, g, b = data[col_x, row_y]
            row += bytes((r, g, b))
        rows.append(bytes(row))
    return rows


def render_to_png(output: Path, width: int = WIDTH, height: int = HEIGHT) -> Path:
    """Render the logo in a Tkinter Canvas and save it to ``output``."""
    import tkinter as tk

    if sys.platform == "win32":
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    root = tk.Tk()
    root.title("SomaiyaSat & SomaiyaPod - Mission Logo Generator")
    root.configure(bg=SPACE_BG)
    root.resizable(False, False)

    canvas = tk.Canvas(
        root, width=width, height=height, bg=SPACE_BG,
        highlightthickness=0, bd=0,
    )
    canvas.pack()

    draw_logo(canvas, width, height)

    # Let Tk finish laying out and painting before we capture.
    root.update_idletasks()
    root.update()

    rows = _capture_widget_hwnd(int(canvas.winfo_id()), width, height)
    if rows is None:
        rows = _capture_via_pillow(root, canvas, width, height)

    root.destroy()

    if rows is None:
        raise RuntimeError(
            "Unable to capture the Tk canvas. Install Pillow or run on Windows."
        )

    write_png(output, width, height, rows)
    return output


def ensure_logo(output: Path = DEFAULT_OUTPUT, force: bool = False) -> Path:
    """Make sure the logo PNG exists, generating it on demand.

    The web dashboard calls this at start-up so ``static/logo.png`` is always
    present and up to date without the user having to run the generator by
    hand. Failures are non-fatal: the dashboard simply reports that the
    artwork is missing.
    """
    output = Path(output)
    if not force and output.exists() and output.stat().st_size > 0:
        return output

    output.parent.mkdir(parents=True, exist_ok=True)
    render_to_png(output)
    return output


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the mission logo PNG.")
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT), help="output PNG path")
    parser.add_argument(
        "--size", default=f"{WIDTH}x{HEIGHT}", help="canvas size, e.g. 900x420"
    )
    parser.add_argument(
        "--print-palette",
        action="store_true",
        help="print the mission colour palette and exit without rendering",
    )
    args = parser.parse_args(argv)

    if args.print_palette:
        for entry in PALETTE:
            print(f"{entry['hex']}  {entry['name']:<16} {entry['role']}")
        return 0

    try:
        width_text, _, height_text = args.size.lower().partition("x")
        width, height = int(width_text), int(height_text)
    except ValueError:
        parser.error("--size must look like 900x420")

    output = Path(args.out)
    if not output.is_absolute():
        output = (Path.cwd() / output).resolve()

    render_to_png(output, width, height)
    size_kb = output.stat().st_size / 1024
    print(f"[logo] wrote {output} ({width}x{height}, {size_kb:.1f} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
