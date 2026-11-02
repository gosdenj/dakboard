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


def _interval_seconds(interval):
    """Resolve an interval to a number of seconds.

    `interval` is either a plain number of seconds or a callable that
    returns one. A callable is how a setting stays in charge of its own
    timing: the value is read again on every tick instead of being frozen
    into the code at startup.
    """
    value = interval() if callable(interval) else interval
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 1.0
    if value <= 0:
        return 0.05
    return value


def _loop(fn, interval, name, state, first_delay=0.0):
    """Run `fn` every `interval` seconds, forever.

    `interval` may be a number of seconds or a callable that returns one.
    A callable is re-read on every tick, so a setting changed on the
    settings page takes effect straight away - in BOTH directions. A longer
    interval is measured from the last run, and a shorter one does not have
    to wait out the old, longer wait first.
    """
    def runner():
        if first_delay:
            time.sleep(first_delay)
        while True:
            try:
                fn()
            except Exception as exc:                      # never die on one bad poll
                sys.stderr.write(f"[{name}] {exc}\n")
            last = time.monotonic()
            while True:
                remaining = last + _interval_seconds(interval) - time.monotonic()
                if remaining <= 0:
                    break
                # Wake up once a second so a shortened interval is noticed
                # straight away instead of after the old wait.
                time.sleep(min(remaining, 1.0))
    thread = threading.Thread(target=runner, name=name, daemon=True)
    thread.start()
    return thread


def _int_setting(config, path, default):
    try:
        return int(config.get(path, default))
    except (TypeError, ValueError):
        return default


def rotate_seconds(config):
    """How long a photo stays on the board. 0 pauses the rotation."""
    return max(0, _int_setting(config, "photos.rotate_seconds", 6))


def rotate_interval(config):
    """The gap between photo changes.

    0 pauses the rotation, but the loop keeps ticking so a new setting can
    start it again.
    """
    secs = rotate_seconds(config)
    return secs if secs > 0 else 1


def photos_interval(config):
    """How often the album is re-read from Google Photos.

    Never less than a minute - hammering the album endpoint gets the
    account blocked, and nothing is gained by asking every second.
    """
    return max(1, _int_setting(config, "photos.refresh_minutes", 30)) * 60


def rotate_once(config, state):
    """Move to the next photo, unless the rotation is paused."""
    if rotate_seconds(config) > 0:
        return state.advance_photo()
    return False


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

    # pollers - the photo timings are read from the config on every tick, so
    # a setting changed on the settings page takes effect without a restart
    _loop(state.poll_calendar, 300, "calendar", state)
    _loop(state.poll_weather, 900, "weather", state)
    _loop(state.poll_photos, lambda: photos_interval(config), "photos", state)

    # photo rotator - the server owns the index so every display agrees
    _loop(lambda: rotate_once(config, state),
          lambda: rotate_interval(config), "rotator", state)

    httpd = serve(config, state, host=host, port=port)
    sys.stderr.write(f"dakboard listening on http://{host}:{port}  (tz={tz_name})\n")

    def shutdown(*_args):
        sys.stderr.write("dakboard shutting down\n")
        # shutdown() waits for serve_forever() to stop, so it must not run
        # on the thread that is running it - that deadlocks, and the board
        # then ignores SIGTERM until something kills it outright.
        threading.Thread(target=httpd.shutdown, daemon=True).start()
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
