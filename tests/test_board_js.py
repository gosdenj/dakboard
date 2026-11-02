"""The board's event rules, tested for what they actually do.

Two rules decide what the wall shows:

  * an event that has finished rolls out of view, and
  * an event that is happening RIGHT NOW belongs under today even when it
    started yesterday.

They live in the JavaScript inside index.html, next to the rest of the
board. They are pure functions - no DOM, no state - so they can be lifted
out of the page and run under node, which tests what they DO rather than
what the file says.

Without this, a rule like "an event with no end time never ends" is
checked by reading the source, which passes just as happily when the
source is wrong.

If node is not installed the test is skipped rather than failed: the board
does not need node to run.
"""

import os
import pathlib
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
BOARD = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

NODE = shutil.which("node")


def _js_function(name):
    """The source of one function from the board's inline script."""
    start = BOARD.index("function %s(" % name)
    body = BOARD[start:]
    return body[:body.index("\n}\n") + 3]


FUNCTIONS = ["localDateKey", "eventEndMs", "isEventOver", "isEventRunning",
             "eventGroupKey"]


@unittest.skipUnless(NODE, "node is not installed")
class EventRuleTests(unittest.TestCase):
    """The rules, run for real."""

    def run_js(self, body):
        source = "\n".join(_js_function(name) for name in FUNCTIONS)
        script = source + "\n" + body
        env = dict(os.environ)
        # the day keys are local dates, so the local zone decides them
        env["TZ"] = "UTC"
        result = subprocess.run([NODE, "-e", script], capture_output=True,
                                text=True, timeout=30, env=env)
        self.assertEqual(result.returncode, 0,
                         "the board's event rules failed:\n%s\n%s"
                         % (result.stdout, result.stderr))
        return result.stdout.strip()

    def test_a_finished_event_is_over(self):
        out = self.run_js("""
        const now = Date.parse("2026-11-02T20:37:00Z");
        const over = {start:"2026-11-02T19:00:00Z", end:"2026-11-02T19:30:00Z"};
        console.log(isEventOver(over, now));
        """)
        self.assertEqual(out, "true")

    def test_an_event_that_is_running_is_not_over(self):
        out = self.run_js("""
        const now = Date.parse("2026-11-02T20:37:00Z");
        const running = {start:"2026-11-02T19:30:00Z", end:"2026-11-02T21:00:00Z"};
        console.log(isEventOver(running, now), isEventRunning(running, now));
        """)
        self.assertEqual(out, "false true")

    def test_an_event_with_no_end_never_ends(self):
        """An event with no end time has no end.

        It used to be treated as ending the moment it started, which would
        have made it vanish before it was ever seen.
        """
        out = self.run_js("""
        const now = Date.parse("2026-11-02T20:37:00Z");
        const open = {start:"2026-11-02T19:30:00Z"};
        console.log(isEventOver(open, now), eventEndMs(open));
        """)
        self.assertEqual(out, "false Infinity")

    def test_a_running_overnight_event_belongs_under_today(self):
        out = self.run_js("""
        const now = Date.parse("2026-11-02T20:37:00Z");
        const today = localDateKey(new Date(now).toISOString());
        const overnight = {start:"2026-11-01T22:00:00Z", end:"2026-11-02T23:00:00Z"};
        console.log(eventGroupKey(overnight, now, today));
        """)
        self.assertEqual(out, "2026-11-02")

    def test_an_event_belongs_under_the_day_it_starts(self):
        out = self.run_js("""
        const now = Date.parse("2026-11-02T20:37:00Z");
        const today = localDateKey(new Date(now).toISOString());
        const tomorrow = {start:"2026-11-03T09:00:00Z", end:"2026-11-03T10:00:00Z"};
        console.log(eventGroupKey(tomorrow, now, today));
        """)
        self.assertEqual(out, "2026-11-03")

    def test_an_overnight_event_that_has_finished_is_not_running(self):
        out = self.run_js("""
        const now = Date.parse("2026-11-02T20:37:00Z");
        const today = localDateKey(new Date(now).toISOString());
        const done = {start:"2026-11-01T22:00:00Z", end:"2026-11-02T06:00:00Z"};
        console.log(isEventRunning(done, now), isEventOver(done, now),
                    eventGroupKey(done, now, today));
        """)
        self.assertEqual(out, "false true 2026-11-01")

    def test_an_event_that_has_not_started_is_not_running(self):
        out = self.run_js("""
        const now = Date.parse("2026-11-02T20:37:00Z");
        const later = {start:"2026-11-02T22:00:00Z", end:"2026-11-02T23:00:00Z"};
        console.log(isEventRunning(later, now), isEventOver(later, now));
        """)
        self.assertEqual(out, "false false")


if __name__ == "__main__":
    unittest.main(verbosity=2)
