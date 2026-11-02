"""Tests for the board page's own behaviour.

The board is one HTML file with its CSS and JS inline. These tests check
the things that are easy to break and hard to notice:

  * the agenda shows as many days as the board is set to show - the
    setting used to change what was FETCHED but not what was DISPLAYED,
    so raising it did nothing you could see;
  * the trim pass puts back what it hid, so shrinking the window does
    not lose events for good;
  * the rows keep their natural height, so content is dropped rather
    than squashed.
"""

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
BOARD = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


class BoardPageTests(unittest.TestCase):
    def test_the_agenda_has_no_hardcoded_day_groups(self):
        """The day groups are built from the setting, not baked into the HTML."""
        markup = re.search(r'<div class="agenda" id="agenda">(.*?)</div>\s*\n', BOARD, re.DOTALL)
        self.assertIsNotNone(markup, "the agenda container is missing")
        self.assertNotIn("eventsTomorrow", markup.group(1),
                         "the Tomorrow group must be built by the script, "
                         "not hardcoded - otherwise days_ahead cannot extend it")
        self.assertNotIn("groupTomorrow", markup.group(1))

    def test_the_agenda_is_built_from_days_ahead(self):
        self.assertIn("function daysAhead(", BOARD)
        self.assertRegex(BOARD, r"days_ahead",
                         "the agenda must read the days_ahead setting")

    def test_every_day_gets_a_group(self):
        """One group per day, with a label the user can read."""
        self.assertIn("function groupEvents(", BOARD)
        self.assertIn("dayLabel(", BOARD)
        self.assertRegex(BOARD, r'"Tomorrow"',
                         "the second day should say Tomorrow")

    def test_the_trim_puts_back_what_it_hid(self):
        """Shrinking the window must not lose events for good.

        The trim hides the rows that do not fit. Without resetting that
        state first, the rows stayed hidden when the window grew again
        and the calendar looked like it had vanished.
        """
        trim = BOARD[BOARD.index("function trimEvents(){"):]
        trim = trim[:trim.index("\n}\n")]
        self.assertIn('row.style.display = ""', trim,
                      "the trim must clear the rows it hid before measuring again")
        self.assertIn('label.style.display = ""', trim,
                      "the trim must clear the headings it hid before measuring again")

    def test_rows_keep_their_natural_height(self):
        """Content is dropped, not squashed.

        A flex row with no flex-shrink:0 shrinks to fit instead of
        overflowing, which leaves the trim pass with nothing to drop and
        crushes the events into unreadable slivers.
        """
        rule = re.search(r"\.ev\{([^}]*)\}", BOARD)
        self.assertIsNotNone(rule)
        self.assertIn("flex-shrink:0", rule.group(1).replace(" ", ""),
                      ".ev must not shrink - the board drops content instead")

    def test_events_are_not_squashed_by_their_group(self):
        for selector in (".group", ".events"):
            rule = re.search(re.escape(selector) + r"\{([^}]*)\}", BOARD)
            self.assertIsNotNone(rule, selector)
            self.assertIn("flex-shrink:0", rule.group(1).replace(" ", ""),
                          "%s must not shrink" % selector)

    def test_the_forecast_icons_are_never_hidden(self):
        """The week strip keeps its icons at every size.

        They used to be display:none below 34rem of height, so a small
        wall tablet lost the forecast icons entirely.
        """
        hidden = re.findall(r"\.strip \.ic\s*\{\s*display:\s*none", BOARD)
        self.assertEqual(hidden, [],
                         "the forecast icons must not be hidden outright - "
                         "they are sized with clamp() instead")

    def test_stream_errors_are_not_swallowed(self):
        """A broken board should say so, not fail silently.

        The stream handlers caught every error and threw it away, which
        is how a missing function could leave the board empty with
        nothing in the console.
        """
        handlers = BOARD[BOARD.index("function connect(){"):BOARD.index("function startPolling(")]
        self.assertNotIn("catch(_){}", handlers,
                         "the stream handlers must log their errors")
        self.assertIn("console.error", handlers)


class PhotoTransitionTests(unittest.TestCase):
    """The photo transition is the part of the board people actually look at.

    Two separate bugs made the transition settings look like they were
    being ignored:

      * the settings page stores "kenburns" but the board looked for
        "ken", so Ken Burns never ran;
      * the arriving photo was not put on top, so every second change
        faded the old photo out over the stylesheet default instead of
        over the time the user asked for.
    """

    def show_photo(self):
        body = BOARD[BOARD.index("function showPhoto("):]
        return body[:body.index("\n}\n")]

    def test_the_transition_name_matches_the_settings_page(self):
        """The settings page stores "kenburns"."""
        self.assertNotIn('transition === "ken"', BOARD,
                         "the board looked for a transition name the "
                         "settings page never sends")
        self.assertIn('"kenburns"', BOARD)

    def test_all_three_transitions_are_different(self):
        """Choosing a transition has to change what the board does."""
        block = BOARD[BOARD.index("const TRANSITIONS = {"):]
        block = block[:block.index("\n};")]
        for name in ("fade", "slide", "kenburns"):
            self.assertIn(name + ":", block, name)
        fade = block[block.index("fade:"):block.index("slide:")]
        slide = block[block.index("slide:"):block.index("kenburns:")]
        self.assertNotEqual(fade, slide,
                            "fade and slide must not be the same transition")

    def test_the_photo_on_screen_is_not_faded_out(self):
        """The photo on screen stays put, under the one arriving.

        Fading both of them out together makes the board dim - and a
        transition longer than the photo interval leaves it half dark.
        """
        show = self.show_photo()
        self.assertNotIn('front.style.opacity = "0"', show,
                         "the photo on screen must not be faded out - the "
                         "arriving one fades in over the top of it")

    def test_the_arriving_photo_fades_in_over_the_one_on_screen(self):
        """That is what makes the transition length visible."""
        show = self.show_photo()
        self.assertIn('back.style.opacity = "1"', show)
        self.assertIn("spec.ms", show)

    def test_the_photo_on_screen_is_hidden_once_the_new_one_is_in_place(self):
        show = self.show_photo()
        self.assertIn('front.style.opacity = ""', show)
        self.assertIn("spec.ms + 80", show)

    def test_the_arriving_photo_goes_on_top(self):
        """Otherwise every second change fades the wrong layer."""
        show = self.show_photo()
        self.assertIn("back.style.zIndex", show)

    def test_a_stale_transition_cannot_tidy_up_the_wrong_layer(self):
        """A slow transition must not blank the board."""
        show = self.show_photo()
        self.assertIn("transitionGen", show,
                      "the cleanup has to know whether it is still the "
                      "current transition")

    def test_the_transition_length_is_not_hardcoded(self):
        """The old code baked the fallback into the stylesheet only."""
        show = self.show_photo()
        self.assertIn("spec.ms", show)
        self.assertNotIn("durationMs + 60", show,
                         "the old cleanup timing was tied to the old "
                         "transition value")


if __name__ == "__main__":
    unittest.main()
