#!/usr/bin/env python3
"""Layout check - render the board at real viewport sizes and assert it holds.

Checks, at every viewport:
  * no horizontal or vertical scroll
  * no element outside the viewport
  * no clipped text
  * no type below the readable floors
  * the layout actually adapts

This is the check that caught the type scale pushing event names to 10.1px on
a 360px phone - the kind of bug that only shows up on the actual screen the
board happens to be on.

Usage:
    python3 tests/layout_check.py            # starts its own server
    python3 tests/layout_check.py --url URL  # use a running board
"""

import argparse
import json
import os
import pathlib
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# (label, width, height) - real devices and plausible kiosk windows
VIEWPORTS = [
    ("phone-portrait",     360,  640),
    ("phone-large",        414,  896),
    ("tablet-portrait",    768, 1024),
    ("tablet-portrait-lg", 810, 1080),
    ("tablet-landscape",  1024,  768),
    ("old-laptop",        1366,  768),
    ("wall-tablet",       1280,  800),
    ("fhd-tv",            1920, 1080),
    ("qhd-tv",            2560, 1440),
    ("ultrawide",         2560, 1080),
    ("4k-tv",             3840, 2160),
    ("pi7-tiny",           800,  480),
    ("small-window",       480,  360),
]

# type floors (px)
FLOORS = {
    "clock": 26, "evname": 11, "evtime": 9.5, "evwhere": 9.5,
    "temp": 15, "label": 9.5, "caption": 9.5, "dn": 8, "tt": 9.5, "date": 10,
}

PROBE = """
() => {
  const de = document.documentElement;
  const vw = de.clientWidth, vh = de.clientHeight;
  const bad = [], clipped = [], tiny = [];
  const floors = __FLOORS__;
  const sel = {
    clock: '.clock .t', evname: '.ev .n', evtime: '.ev .t', evwhere: '.ev .w',
    temp: '.wx .temp', label: '.label', caption: '.caption',
    dn: '.strip .dn', tt: '.strip .tt', date: '.clock .d'
  };
  for (const [k, s] of Object.entries(sel)) {
    document.querySelectorAll(s).forEach(n => {
      if (getComputedStyle(n).display === 'none') return;
      const fs = parseFloat(getComputedStyle(n).fontSize);
      if (fs && fs < floors[k] - 0.01) tiny.push(k + ':' + fs.toFixed(1));
      if (n.scrollWidth > n.clientWidth + 1 && n.clientWidth > 0)
        clipped.push(k + ':' + n.textContent.trim().slice(0, 22));
    });
  }
  document.querySelectorAll('body *').forEach(n => {
    if (n.closest('.stack') || n.tagName === 'IMG' || n.closest('.veil')) return;
    const r = n.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return;
    if (r.bottom > vh + 1.5 || r.right > vw + 1.5 || r.top < -1.5 || r.left < -1.5)
      bad.push((n.className || n.tagName));
  });
  const board = document.querySelector('.board');
  const cols = getComputedStyle(board).gridTemplateColumns;
  return {
    scrollX: de.scrollWidth - vw, scrollY: de.scrollHeight - vh,
    oob: bad.slice(0, 4), nOob: bad.length,
    clipped: clipped.slice(0, 4), nClip: clipped.length,
    tiny: tiny.slice(0, 4), nTiny: tiny.length,
    stacked: cols.split(' ').length === 1,
    evs: document.querySelectorAll('.ev').length,
    groups: document.querySelectorAll('.group').length,
    labels: Array.from(document.querySelectorAll('.group .label')).map(l => l.textContent),
    clock: (document.querySelector('.clock .t') || {}).textContent
  };
}
"""


def start_server(port, data_dir):
    env = dict(os.environ)
    env["DAKBOARD_DATA"] = data_dir
    env["DAKBOARD_PORT"] = str(port)
    env["DAKBOARD_TZ"] = "Europe/London"
    proc = subprocess.Popen(
        [sys.executable, "-B", "-u", "-m", "dakboard"],
        cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        time.sleep(0.5)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2):
                return proc
        except Exception:
            continue
    proc.terminate()
    raise RuntimeError("the board server did not start")


# --------------------------------------------------------------------------
# A synthetic calendar, so the layout check exercises a board with real
# events instead of an empty agenda. An empty agenda hides every overflow
# bug the check exists to find.
# --------------------------------------------------------------------------
SAMPLE_EVENTS = [
    ("Swimming",          "09:30", "Leisure Centre",      0),
    ("Lunch with Nan",    "12:15", "The Green Man",       0),
    ("Football practice", "16:00", "Meadow Park",         0),
    ("School parents evening", "19:30", "Main Hall",      0),
    ("Curry night",       "21:00", "Home",                0),
    ("Dentist",           "08:00", "Bright Smile",        1),
    ("Baby group",        "10:30", "Church hall",         1),
    ("Football match",    "18:00", "Meadow Park",         1),
]


def _ics():
    import datetime
    tz = datetime.timezone(datetime.timedelta(hours=1))
    today = datetime.datetime.now(tz).date()
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0",
             "PRODID:-//dakboard//layout check//EN"]
    for summary, hhmm, where, day_offset in SAMPLE_EVENTS:
        day = today + datetime.timedelta(days=day_offset)
        hh, mm = hhmm.split(":")
        start = datetime.datetime(day.year, day.month, day.day, int(hh), int(mm), tzinfo=tz)
        end = start + datetime.timedelta(hours=1)
        lines += [
            "BEGIN:VEVENT",
            f"UID:{summary.replace(' ', '')}-{day.isoformat()}@dakboard",
            f"DTSTAMP:{start.strftime('%Y%m%dT%H%M%S')}",
            f"DTSTART;TZID=Europe/London:{start.strftime('%Y%m%dT%H%M%S')}",
            f"DTEND;TZID=Europe/London:{end.strftime('%Y%m%dT%H%M%S')}",
            f"SUMMARY:{summary}",
            f"LOCATION:{where}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


class _IcsHandler(__import__("http.server", fromlist=["x"]).BaseHTTPRequestHandler):
    def do_GET(self):
        body = _ics().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/calendar; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def start_ics_server():
    """Serve a synthetic calendar on a free port. Returns (server, url)."""
    import http.server
    import socketserver
    srv = socketserver.TCPServer(("127.0.0.1", 0), _IcsHandler)
    srv.allow_reuse_address = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/calendar.ics"


def write_config(data_dir, ical_url, layout):
    """Seed the board with a calendar and a layout so the check has content."""
    import json
    cfg = {
        "calendar": {"mode": "ical", "ical_url": ical_url, "days_ahead": 3},
        "display": {"layout": layout},
        "weather": {"place": "Welwyn Garden City"},
    }
    with open(os.path.join(data_dir, "config.json"), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="check a board that is already running")
    ap.add_argument("--port", type=int, default=8199)
    ap.add_argument("--shots", help="directory to write screenshots into")
    ap.add_argument("--layouts", default="A,B,C",
                    help="which layouts to check (comma separated)")
    args = ap.parse_args()

    proc = None
    data_dir = None
    ics_srv = None
    layouts = [x.strip().upper() for x in args.layouts.split(",") if x.strip()]
    if args.url:
        base = args.url.rstrip("/")
    else:
        import tempfile
        ics_srv, ical_url = start_ics_server()
        data_dir = tempfile.mkdtemp(prefix="dakboard-layout-")
        write_config(data_dir, ical_url, layouts[0])
        proc = start_server(args.port, data_dir)
        base = f"http://127.0.0.1:{args.port}"

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is needed for the layout check: pip install playwright")
        if proc:
            proc.terminate()
        if ics_srv:
            ics_srv.shutdown()
        return 2

    probe = PROBE.replace("__FLOORS__", json.dumps(FLOORS))
    failures = []
    shots = pathlib.Path(args.shots) if args.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
            for layout in layouts:
                print(f"  --- layout {layout} ---")
                for label, w, h in VIEWPORTS:
                    page = browser.new_page(viewport={"width": w, "height": h})
                    errors = []
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    page.goto(base + "/", wait_until="networkidle", timeout=60000)
                    page.wait_for_timeout(2500)
                    # force the layout under test
                    page.evaluate(
                        "(l) => { document.getElementById('board').dataset.layout = l; }",
                        layout)
                    page.wait_for_timeout(800)
                    r = page.evaluate(probe)
                    ok = (r["scrollX"] <= 1 and r["scrollY"] <= 1 and r["nOob"] == 0
                          and r["nClip"] == 0 and r["nTiny"] == 0 and not errors
                          and r["evs"] > 0
                          # the agenda must actually have its day groups -
                          # an empty agenda overflows nothing and looks fine
                          and r["groups"] >= 2
                          and r["labels"][0] == "Today")
                    mode = "stacked" if r["stacked"] else "split"
                    flag = "PASS" if ok else "FAIL"
                    print(f"  {flag} {label:<20} {w:>5}x{h:<5} {mode:<8} events={r['evs']:<3} "
                          f"days={r['groups']:<2} "
                          f"scroll={r['scrollX']}/{r['scrollY']} oob={r['nOob']} "
                          f"clip={r['nClip']} tiny={r['nTiny']}")
                    if not ok:
                        failures.append(
                            f"layout {layout} / {label}: scroll={r['scrollX']}/{r['scrollY']} "
                            f"oob={r['nOob']}{r['oob']} clip={r['nClip']}{r['clipped']} "
                            f"tiny={r['nTiny']}{r['tiny']} events={r['evs']} "
                            f"groups={r['groups']} labels={r['labels']}"
                            + (f" js={errors}" if errors else ""))
                    if shots:
                        page.screenshot(path=str(shots / f"vp-{layout}-{label}.png"))
                    page.close()
            browser.close()
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
        if ics_srv:
            ics_srv.shutdown()

    print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print("  -", f)
        return 1
    print(f"All {len(VIEWPORTS) * len(layouts)} checks pass "
          f"({len(VIEWPORTS)} viewports x {len(layouts)} layouts): no scroll, "
          "no out-of-bounds, no clipped text, no sub-floor type.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
