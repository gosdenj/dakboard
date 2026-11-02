"""Tests for the board's CSS.

The board looked BROKEN and every layout check passed. The reason: the
stylesheet used `var(--pad)` in half a dozen places, and `--pad` was never
defined. An undefined custom property makes the whole declaration invalid
at computed-value time, so `padding: var(--pad)` silently became
`padding: 0` - the data panel had no gutter and the photo caption sat in
the corner. Nothing overflowed, so nothing failed.

A layout check that only measures overflow cannot see this class of bug.
These tests can.
"""

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"


def _css_text():
    """Every stylesheet the board uses, as one string."""
    parts = []
    for name in ("base.css", "index.html", "settings.html"):
        parts.append((STATIC / name).read_text(encoding="utf-8"))
    return "\n".join(parts)


class CssVariableTests(unittest.TestCase):
    def test_no_undefined_custom_properties(self):
        """Every var(--x) must have a --x: definition somewhere.

        An undefined custom property does not fall back to anything useful:
        the declaration using it is invalid at computed-value time and is
        dropped, so `padding: var(--pad)` becomes `padding: 0` with no
        warning anywhere.
        """
        text = _css_text()
        defined = set(re.findall(r"(--[a-z0-9-]+)\s*:", text))
        used = set(re.findall(r"var\(\s*(--[a-z0-9-]+)", text))
        missing = sorted(used - defined)
        self.assertEqual(
            missing, [],
            "undefined CSS variable(s) used but never defined: %s. "
            "A var() with no definition silently drops its declaration."
            % ", ".join(missing))

    def test_the_board_padding_is_defined(self):
        """The gutter the data panel depends on."""
        text = (STATIC / "base.css").read_text(encoding="utf-8")
        self.assertIn("--pad:", text,
                      "--pad is used for the panel padding and the photo caption")
        self.assertRegex(text, r"--pad:\s*calc\(",
                         "--pad should be derived from --u so it scales with the screen")

    def test_the_unit_has_a_floor(self):
        """--u must keep a minimum, or the type floors are not enforced."""
        text = (STATIC / "base.css").read_text(encoding="utf-8")
        m = re.search(r"--u:\s*clamp\(([^;]+);", text)
        self.assertIsNotNone(m, "--u should be a clamp()")
        self.assertIn("rem", m.group(1),
                      "--u needs a rem-based minimum so small screens do not "
                      "shrink the type below the floors")


class BoardMarkupTests(unittest.TestCase):
    def test_the_board_page_defines_the_panel_gutter(self):
        """The data panel must be padded - this is the visible bug."""
        text = (STATIC / "index.html").read_text(encoding="utf-8")
        m = re.search(r"\.data\{([^}]*)\}", text)
        self.assertIsNotNone(m, "no .data rule found")
        self.assertIn("padding:var(--pad)", m.group(1).replace(" ", ""),
                      "the data panel needs padding:var(--pad) for its gutter")

    def test_the_photo_caption_is_inset(self):
        """The caption sits over the photo, so it needs its own inset."""
        text = (STATIC / "index.html").read_text(encoding="utf-8")
        m = re.search(r"\.caption\{([^}]*)\}", text)
        self.assertIsNotNone(m, "no .caption rule found")
        rule = m.group(1).replace(" ", "")
        self.assertIn("left:var(--pad)", rule)
        self.assertIn("bottom:var(--pad)", rule)


if __name__ == "__main__":
    unittest.main()
