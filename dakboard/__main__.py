"""Board entry point.

Starts the pollers, the photo rotator and the HTTP server.
"""

import datetime
import os
import signal
import sys
import threading
import time
from zoneinfo import ZoneInfo

from .config import ConfigStore
from .server import serve
from .state import BoardState


def _loop(fn, interval, name, state, first_delay=0.0):
    """Run `fn` every `interval` seconds, forever."""
    def runner():
        if first_delay:
            time.sleep(first_delay)
        while True:
            try:
                fn()
            except Exception as exc:                      # never die on one bad poll
                sys.stderr.write(f"[{name}] {exc}\n")
            time.sleep(interval)
    thread = threading.Thread(target=runner, name=name, daemon=True)
    thread.start()
    return thread


def main():
    tz_name = os.environ.get("DAKBOARD_TZ", "Europe/London")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = datetime.timezone.utc

    config = ConfigStore()
    state = BoardState(config, tz=tz)

    host = os.environ.get("DAKBOARD_HOST", "0.0.0.0")
    port = int(os.environ.get("DAKBOARD_PORT", "8099"))

    # first poll straight away so the board is not blank on first load
    threading.Thread(target=state.poll_calendar, daemon=True).start()
    threading.Thread(target=state.poll_weather, daemon=True).start()
    threading.Thread(target=state.poll_photos, daemon=True).start()

    # pollers
    _loop(state.poll_calendar, 300, "calendar", state)
    _loop(state.poll_weather, 900, "weather", state)
    _loop(state.poll_photos, 1800, "photos", state)

    # photo rotator - the server owns the index so every display agrees
    def rotate():
        seconds = int(config.get("photos.rotate_seconds", 6) or 6)
        if seconds > 0:
            state.advance_photo()
    _loop(rotate, 6, "rotator", state)

    httpd = serve(config, state, host=host, port=port)
    sys.stderr.write(f"dakboard listening on http://{host}:{port}  (tz={tz_name})\n")

    def shutdown(*_args):
        sys.stderr.write("dakboard shutting down\n")
        httpd.shutdown()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
