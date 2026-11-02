"""Tests for the board's own timing.

The settings page offered "change photo every" and "re-read the album
every", both saved, both showed up in the settings page as saved - and
neither of them did anything. The rotator ran on a 6 second loop and the
album was re-read every 30 minutes, whatever the settings said, because
both intervals were written into the code when the app started.

A setting that is stored and ignored is worse than a setting that is
missing: it looks like it worked.
"""

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dakboard.__main__ import (  # noqa: E402
    _interval_seconds,
    _loop,
    photos_interval,
    rotate_interval,
    rotate_once,
    rotate_seconds,
)
from dakboard.config import ConfigStore  # noqa: E402


class FakeState:
    """Just enough state to watch the rotator."""

    def __init__(self):
        self.advances = 0

    def advance_photo(self):
        self.advances += 1
        return True


def _config(**values):
    tmp = tempfile.mkdtemp()
    config = ConfigStore(os.path.join(tmp, "config.json"))
    for path, value in values.items():
        config.set(path.replace("__", "."), value)
    return config


class IntervalTests(unittest.TestCase):
    def test_a_plain_number_is_used_as_it_is(self):
        self.assertEqual(_interval_seconds(30), 30.0)

    def test_a_callable_is_asked_for_the_interval(self):
        self.assertEqual(_interval_seconds(lambda: 12), 12.0)

    def test_a_callable_is_asked_again_each_time(self):
        """The interval has to be re-read, or a changed setting is ignored."""
        values = [5, 9]
        self.assertEqual(_interval_seconds(lambda: values[0]), 5.0)
        self.assertEqual(_interval_seconds(lambda: values[0]), 5.0)
        values[0] = 9
        self.assertEqual(_interval_seconds(lambda: values[0]), 9.0)

    def test_nonsense_does_not_stop_the_loop(self):
        for bad in (None, "abc", object()):
            self.assertGreater(_interval_seconds(bad), 0)

    def test_zero_does_not_become_a_busy_loop(self):
        self.assertGreater(_interval_seconds(0), 0)


class RotateTimingTests(unittest.TestCase):
    def test_the_setting_decides_how_long_a_photo_stays(self):
        config = _config(photos__rotate_seconds=45)
        self.assertEqual(rotate_seconds(config), 45)
        self.assertEqual(rotate_interval(config), 45)

    def test_the_default_is_the_default(self):
        config = _config()
        self.assertEqual(rotate_seconds(config), 6)

    def test_zero_pauses_the_rotation_but_keeps_the_loop_ticking(self):
        config = _config(photos__rotate_seconds=0)
        self.assertEqual(rotate_seconds(config), 0)
        # the loop has to keep ticking, or a new setting could never
        # start the rotation again
        self.assertEqual(rotate_interval(config), 1)

    def test_a_paused_rotation_does_not_change_the_photo(self):
        config = _config(photos__rotate_seconds=0)
        state = FakeState()
        rotate_once(config, state)
        self.assertEqual(state.advances, 0)

    def test_a_running_rotation_changes_the_photo(self):
        config = _config(photos__rotate_seconds=20)
        state = FakeState()
        rotate_once(config, state)
        self.assertEqual(state.advances, 1)

    def test_a_broken_value_falls_back_to_the_default(self):
        config = _config()
        config._data["photos"]["rotate_seconds"] = "not a number"
        self.assertEqual(rotate_seconds(config), 6)


class PhotoRefreshTests(unittest.TestCase):
    def test_the_setting_decides_how_often_the_album_is_re_read(self):
        config = _config(photos__refresh_minutes=5)
        self.assertEqual(photos_interval(config), 300)

    def test_the_default_is_thirty_minutes(self):
        config = _config()
        self.assertEqual(photos_interval(config), 1800)

    def test_it_never_asks_for_the_album_more_than_once_a_minute(self):
        """Asking every second gets the account blocked."""
        config = _config(photos__refresh_minutes=0)
        self.assertEqual(photos_interval(config), 60)


class LoopTests(unittest.TestCase):
    def test_the_loop_re_reads_its_interval(self):
        """A changed interval has to take effect without a restart."""
        calls = []
        wait = [0.05]

        def fn():
            calls.append(time.monotonic())

        _loop(fn, lambda: wait[0], "test-loop", None)
        time.sleep(0.5)
        first = len(calls)
        self.assertGreaterEqual(first, 3, "the loop should have run several times")

        # now make it slow - the loop has to slow down with it
        wait[0] = 30
        time.sleep(0.4)
        self.assertEqual(len(calls), first,
                         "the loop ignored the new interval")

    def test_a_shortened_interval_is_noticed_straight_away(self):
        """It must not have to wait out the old, longer interval first."""
        calls = []
        wait = [30]

        def fn():
            calls.append(time.monotonic())

        _loop(fn, lambda: wait[0], "test-loop-fast", None)
        time.sleep(0.2)
        wait[0] = 0.05
        deadline = time.monotonic() + 3
        while len(calls) < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertGreaterEqual(len(calls), 2,
                                "a shortened interval has to be noticed at once")

    def test_a_failing_call_does_not_kill_the_loop(self):
        """One bad poll must not stop the board updating."""
        calls = []

        def fn():
            calls.append(1)
            raise RuntimeError("the network is down")

        _loop(fn, 0.05, "test-loop-fail", None)
        time.sleep(0.5)
        self.assertGreaterEqual(len(calls), 3)


class ShutdownTests(unittest.TestCase):
    def test_sigterm_stops_the_board_promptly(self):
        """A board that ignores SIGTERM takes ten seconds to stop.

        Docker sends SIGTERM and waits before killing the container, so a
        board that hangs on it makes every redeploy slow. It hung because
        shutdown() was called on the very thread running serve_forever().
        """
        import socket
        import signal
        import subprocess

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

        tmp = tempfile.mkdtemp()
        env = dict(os.environ)
        env["DAKBOARD_DATA"] = tmp
        env["DAKBOARD_CONFIG"] = os.path.join(tmp, "config.json")
        env["DAKBOARD_PORT"] = str(port)
        env["DAKBOARD_HOST"] = "127.0.0.1"

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        proc = subprocess.Popen(
            [sys.executable, "-B", "-m", "dakboard"],
            env=env, cwd=root,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            # wait for it to start listening
            deadline = time.monotonic() + 10
            ready = False
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    break
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                        ready = True
                        break
                except OSError:
                    time.sleep(0.1)
            self.assertTrue(ready, "the board never started listening")

            started = time.monotonic()
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=15)
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 8,
                            "the board took %.1fs to stop - it is hanging on "
                            "SIGTERM" % elapsed)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
